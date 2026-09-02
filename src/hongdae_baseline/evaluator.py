from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import traceback
from typing import Any, Iterable
import uuid

from .assets import AssetManifest, configure_sumo_runtime, find_sumo_binary, runtime_environment
from .config import CONTROLLERS, EvaluationConfig
from .legacy_v5 import custom_hongdae_observation
from .metrics import VehicleMetrics, write_results_csv
from .policy import CONTROLLER_ASSETS, DeterministicPPOPolicy
from .route import count_generated_vehicles, generate_route, route_sha256, validate_route_edges
from .seeds import SeedBundle
from .signal import PPOPhaseController, TLSDefinition


class EvaluationBlocked(RuntimeError):
    pass


def verify_loaded_route(connection: Any, requested_route: Path) -> str:
    """Assert that SUMO reports exactly the route requested at session reset/start."""
    try:
        option = str(connection.simulation.getOption("route-files"))
    except Exception as exc:  # pragma: no cover - depends on external SUMO version
        raise RuntimeError("SUMO cannot report the effective 'route-files' option") from exc
    values = [value.strip() for value in option.split(",") if value.strip()]
    actual = [Path(value).resolve() for value in values]
    requested = requested_route.resolve()
    if actual != [requested]:
        raise RuntimeError(f"SUMO route mismatch: requested {requested}, effective {actual}")
    return str(actual[0])


def build_sumo_command(
    binary: Path,
    config: EvaluationConfig,
    route: Path,
    tripinfo: Path,
    seed: SeedBundle,
) -> list[str]:
    command = [
        str(binary),
        "--net-file",
        str(config.network),
        "--route-files",
        str(route.resolve()),
        "--end",
        str(config.horizon_seconds),
        "--time-to-teleport",
        str(config.time_to_teleport),
        "--tripinfo-output",
        str(tripinfo),
        "--tripinfo-output.write-unfinished",
        "true",
        "--ignore-route-errors",
        "true",
        "--pedestrian.model",
        "striping",
        "--no-step-log",
        "true",
        "--duration-log.disable",
        "true",
    ]
    if seed.sumo == "random":
        command.append("--random")
    else:
        command.extend(("--seed", str(seed.sumo)))
    return command


def _install_fixed_time(connection: Any, definition: TLSDefinition) -> dict[str, object]:
    import traci

    phases = [traci.trafficlight.Phase(phase.duration, phase.state) for phase in definition.native_phases]
    logic = traci.trafficlight.Logic("hongdae_fixed_time", 0, 0, phases=phases)
    connection.trafficlight.setProgramLogic(definition.tls_id, logic)
    connection.trafficlight.setProgram(definition.tls_id, "hongdae_fixed_time")
    connection.trafficlight.setPhase(definition.tls_id, 0)
    current_program = connection.trafficlight.getProgram(definition.tls_id)
    if current_program != "hongdae_fixed_time":
        raise RuntimeError(f"Failed to activate fixed-time program; active={current_program}")
    return {
        "program_id": current_program,
        "type": "static",
        "phase_durations": [phase.duration for phase in definition.native_phases],
        "phase_states": [phase.state for phase in definition.native_phases],
    }


def _actuated_runtime(connection: Any, definition: TLSDefinition) -> dict[str, object]:
    if definition.native_type != "actuated":
        raise RuntimeError(
            f"ACTUATED requested, but network TLS {definition.tls_id} type is {definition.native_type!r}"
        )
    program = connection.trafficlight.getProgram(definition.tls_id)
    logic = next(
        (item for item in connection.trafficlight.getAllProgramLogics(definition.tls_id) if item.programID == program),
        None,
    )
    return {
        "program_id": program,
        "type": "actuated",
        "traci_logic_type": getattr(logic, "type", None),
        "network_type": definition.native_type,
    }


def evaluate_controller(
    config: EvaluationConfig,
    controller: str,
    route: Path,
    seed: SeedBundle,
    output_dir: Path,
    assets: AssetManifest,
) -> tuple[dict[str, object], dict[str, object]]:
    if controller not in CONTROLLERS:
        raise ValueError(f"Unknown controller {controller!r}")
    configure_sumo_runtime()
    binary = find_sumo_binary("sumo")
    if binary is None:
        raise EvaluationBlocked(
            "SUMO executable is not installed or discoverable. Install the official macOS SUMO 1.27.1 pkg, "
            "then ensure sumo is on PATH and SUMO_HOME points to its share/sumo directory."
        )
    route = route.resolve()
    validate_route_edges(route, config.network)
    output_dir.mkdir(parents=True, exist_ok=True)
    tripinfo = output_dir / "tripinfo.xml"
    command = build_sumo_command(binary, config, route, tripinfo, seed)
    definition = TLSDefinition.from_network(config.network, config.tls_id)
    policy = DeterministicPPOPolicy.load(assets, controller) if controller in CONTROLLER_ASSETS else None
    phase_controller: PPOPhaseController | None = None
    runtime_controller: dict[str, object] = {}
    throughput = 0
    departed_vehicle_count = 0
    max_queue = 0
    teleports = 0
    final_network_vehicle_count = 0
    generated_vehicle_count = count_generated_vehicles(route)
    label = f"hongdae_{uuid.uuid4().hex}"
    connection = None
    effective_route = None
    effective_sumo_seed: str | int | None = None
    controlled_lanes: tuple[str, ...] = ()
    import traci

    try:
        traci.start(command, label=label)
        connection = traci.getConnection(label)
        effective_route = verify_loaded_route(connection, route)
        try:
            effective_sumo_seed = connection.simulation.getOption("seed")
        except Exception:
            effective_sumo_seed = seed.sumo
        runtime_lanes = tuple(dict.fromkeys(connection.trafficlight.getControlledLanes(config.tls_id)))
        if runtime_lanes != definition.incoming_lanes:
            raise RuntimeError(
                f"Controlled lane order mismatch: network={definition.incoming_lanes}, runtime={runtime_lanes}"
            )
        controlled_lanes = runtime_lanes
        lane_lengths = {lane: float(connection.lane.getLength(lane)) for lane in controlled_lanes}

        if controller == "FIXED_TIME":
            runtime_controller = _install_fixed_time(connection, definition)
        elif controller == "ACTUATED":
            runtime_controller = _actuated_runtime(connection, definition)
        else:
            phase_controller = PPOPhaseController(
                definition.green_states,
                min_green=config.min_green,
                yellow_time=config.yellow_time,
                max_green=config.max_green,
                enforce_max_green=config.enforce_max_green,
            )
            set_state = lambda state: connection.trafficlight.setRedYellowGreenState(config.tls_id, state)
            phase_controller.initialize(set_state)
            initial_observation = custom_hongdae_observation(
                connection,
                controlled_lanes,
                lane_lengths,
                phase_controller.observation_phase,
                len(definition.green_states),
            )
            phase_controller.request_phase(policy.predict(initial_observation), set_state)
            runtime_controller = {
                "type": "ppo_direct_traci",
                "yellow_time": config.yellow_time,
                "max_green": config.max_green,
                "max_green_enforced": config.enforce_max_green,
                "delta_time": config.delta_time,
                "min_green": config.min_green,
                "green_states": list(definition.green_states),
            }

        for second in range(1, config.horizon_seconds + 1):
            connection.simulationStep()
            if phase_controller is not None:
                phase_controller.tick(set_state)
            throughput += int(connection.simulation.getArrivedNumber())
            departed_vehicle_count += int(connection.simulation.getDepartedNumber())
            teleports += len(connection.simulation.getStartingTeleportIDList())
            queue = sum(int(connection.lane.getLastStepHaltingNumber(lane)) for lane in controlled_lanes)
            max_queue = max(max_queue, queue)
            if phase_controller is not None and second % config.delta_time == 0 and second < config.horizon_seconds:
                observation = custom_hongdae_observation(
                    connection,
                    controlled_lanes,
                    lane_lengths,
                    phase_controller.observation_phase,
                    len(definition.green_states),
                )
                phase_controller.request_phase(policy.predict(observation), set_state)
        if phase_controller is not None:
            runtime_controller["forced_transition_count"] = phase_controller.forced_transitions
        final_network_vehicle_count = int(connection.vehicle.getIDCount())
    finally:
        if connection is not None:
            connection.close()

    metrics = VehicleMetrics.from_tripinfo(
        tripinfo,
        throughput=throughput,
        generated_vehicle_count=generated_vehicle_count,
        departed_vehicle_count=departed_vehicle_count,
        arrived_vehicle_count=throughput,
        final_network_vehicle_count=final_network_vehicle_count,
        max_queue=max_queue,
        teleport_count=teleports,
    )
    model_fields = policy.manifest_fields() if policy is not None else {
        "checkpoint": "",
        "checkpoint_sha256": "",
        "vecnormalize": "",
        "vecnormalize_sha256": "",
    }
    record: dict[str, object] = {
        "mode": config.mode,
        "controller": controller,
        "seed": seed.master,
        "master_seed": seed.master,
        "python_seed": seed.python,
        "numpy_seed": seed.numpy,
        "torch_seed": seed.torch,
        "random_trips_seed": seed.random_trips,
        "duarouter_seed": seed.duarouter,
        "route_seed": seed.route,
        "env_seed": seed.env,
        "route_hash": route_sha256(route),
        "route_file": str(route),
        "sumo_seed": effective_sumo_seed,
        **model_fields,
        **metrics.to_dict(),
    }
    detail = {
        "controller": controller,
        "seed_bundle": seed.to_dict(),
        "command": command,
        "requested_route": str(route),
        "effective_route": effective_route,
        "route_hash": record["route_hash"],
        "effective_sumo_seed": effective_sumo_seed,
        "controlled_lanes": list(controlled_lanes),
        "runtime_controller": runtime_controller,
        "metrics": metrics.to_dict(),
        "metric_population": {
            "waiting_time_and_time_loss": (
                "all departed vehicles written to tripinfo, including vehicles unfinished at the 500-second horizon"
            ),
            "throughput": "identical to arrived_vehicle_count",
            "unfinished_vehicle_count": "generated_vehicle_count minus arrived_vehicle_count",
        },
        "model": model_fields,
    }
    return record, detail


def _write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_experiment(
    config: EvaluationConfig,
    controllers: Iterable[str],
    seeds: Iterable[int],
    output_dir: Path | None = None,
    supplied_route: Path | None = None,
) -> Path:
    controllers = tuple(controllers)
    seeds = tuple(int(seed) for seed in seeds)
    if not controllers or not seeds:
        raise ValueError("At least one controller and seed are required")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = (output_dir or config.results_dir / f"{config.mode}_{run_id}").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    assets = AssetManifest(config.asset_manifest, config.root)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "status": "running",
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": config.to_manifest(),
        "controllers": list(controllers),
        "seeds": list(seeds),
        "runtime_environment": runtime_environment(),
        "verified_asset_hashes": assets.verify_all(),
        "routes": [],
        "evaluations": [],
        "limitations": [
            "sample_legacy_routes generates vehicle-only demand from the representative legacy route",
            "legacy_compatible preserves 2-second generated yellow and ignored max_green for PPO",
            "historical notebook FIXED was actuated; this harness always keeps FIXED_TIME and ACTUATED distinct",
            "waiting-time and time-loss statistics include departed vehicles unfinished at the evaluation horizon",
        ],
        "metric_definitions": {
            "throughput": "arrived_vehicle_count",
            "completion_rate": "arrived_vehicle_count / generated_vehicle_count",
            "unfinished_vehicle_count": "generated_vehicle_count - arrived_vehicle_count",
            "waiting_time_and_time_loss_population": (
                "all departed tripinfo records, including vehicles unfinished at the evaluation horizon"
            ),
        },
    }
    manifest_path = output_dir / "run_manifest.json"
    _write_json(manifest_path, manifest)
    records: list[dict[str, object]] = []
    write_results_csv(output_dir / "results.csv", records)
    try:
        routes_by_seed: dict[int, Path] = {}
        for master_seed in seeds:
            seed = SeedBundle.from_master(master_seed, config.sumo_seed_mode)
            seed.apply_process_seeds()
            if supplied_route is None:
                generated = generate_route(
                    config.route_template,
                    output_dir / "routes" / f"seed_{master_seed}.rou.xml",
                    master_seed,
                    config.demand_end_seconds,
                    config.vehicle_period_seconds,
                )
                route = generated.path
                route_manifest = generated.to_manifest()
            else:
                route = supplied_route.resolve()
                route_manifest = {
                    "path": str(route),
                    "sha256": route_sha256(route),
                    "master_seed": master_seed,
                    "generator": "supplied_fixed_route",
                }
            manifest["routes"].append(route_manifest)
            routes_by_seed[master_seed] = route
        _write_json(manifest_path, manifest)

        for master_seed in seeds:
            seed = SeedBundle.from_master(master_seed, config.sumo_seed_mode)
            seed.apply_process_seeds()
            route = routes_by_seed[master_seed]
            route_hashes: set[str] = set()
            for controller in controllers:
                record, detail = evaluate_controller(
                    config,
                    controller,
                    route,
                    seed,
                    output_dir / f"seed_{master_seed}" / controller,
                    assets,
                )
                records.append(record)
                manifest["evaluations"].append(detail)
                route_hashes.add(str(record["route_hash"]))
                write_results_csv(output_dir / "results.csv", records)
            if len(route_hashes) != 1:
                raise AssertionError(f"Controllers did not share one route hash for seed {master_seed}: {route_hashes}")
        manifest["status"] = "complete"
        manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        manifest["status"] = "blocked" if isinstance(exc, EvaluationBlocked) else "failed"
        manifest["error"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
        raise
    finally:
        _write_json(manifest_path, manifest)
    return output_dir
