from __future__ import annotations

from dataclasses import replace
import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hongdae_baseline.assets import AssetManifest, configure_sumo_runtime, find_sumo_binary
from hongdae_baseline.config import CONTROLLERS, load_config
from hongdae_baseline.evaluator import _install_fixed_time, build_sumo_command, run_experiment, verify_loaded_route
from hongdae_baseline.metrics import RESULT_COLUMNS, write_results_csv
from hongdae_baseline.route import generate_route, route_sha256
from hongdae_baseline.seeds import SeedBundle


ROOT = Path(__file__).resolve().parents[1]


class _Simulation:
    def __init__(self, route: Path):
        self.route = route

    def getOption(self, name: str) -> str:
        if name != "route-files":
            raise KeyError(name)
        return str(self.route)


class _Connection:
    def __init__(self, route: Path):
        self.simulation = _Simulation(route)


class _TrafficLight:
    def __init__(self):
        self.logic = None
        self.program = "0"

    def setProgramLogic(self, tls_id, logic):
        self.logic = logic

    def setProgram(self, tls_id, program):
        self.program = program

    def setPhase(self, tls_id, phase):
        self.phase = phase

    def getProgram(self, tls_id):
        return self.program


class _FixedConnection:
    def __init__(self):
        self.trafficlight = _TrafficLight()


class EvaluationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config("corrected_baseline", ROOT)

    def test_reset_route_verification_matches_requested_file(self) -> None:
        requested = self.config.route_template.resolve()
        self.assertEqual(verify_loaded_route(_Connection(requested), requested), str(requested))
        with self.assertRaises(RuntimeError):
            verify_loaded_route(_Connection(ROOT / "wrong.rou.xml"), requested)

    def test_paired_controllers_receive_identical_route_hash(self) -> None:
        def fake_evaluate(config, controller, route, seed, output_dir, assets):
            digest = route_sha256(route)
            record = {
                "mode": config.mode,
                "controller": controller,
                "seed": seed.master,
                "master_seed": seed.master,
                "route_hash": digest,
                "route_file": str(route),
                "sumo_seed": seed.sumo,
                "checkpoint": controller if controller.startswith("PPO") else "",
                "checkpoint_sha256": "",
                "vecnormalize": "",
                "vecnormalize_sha256": "",
                "avg_vehicle_waiting_time": 0,
                "p95_vehicle_waiting_time": 0,
                "max_vehicle_waiting_time": 0,
                "avg_time_loss": 0,
                "throughput": 0,
                "max_queue": 0,
                "teleport_count": 0,
            }
            return record, {"controller": controller, "route_hash": digest}

        with tempfile.TemporaryDirectory() as directory, patch(
            "hongdae_baseline.evaluator.evaluate_controller", side_effect=fake_evaluate
        ):
            output = run_experiment(self.config, CONTROLLERS, [101], Path(directory) / "run")
            with (output / "results.csv").open(encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 4)
            self.assertEqual(len({row["route_hash"] for row in rows}), 1)
            self.assertEqual({row["controller"] for row in rows}, set(CONTROLLERS))

    def test_result_csv_has_required_identity_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.csv"
            write_results_csv(path, [{column: "x" for column in RESULT_COLUMNS}])
            with path.open(encoding="utf-8", newline="") as handle:
                header = next(csv.reader(handle))
            for required in (
                "controller",
                "route_hash",
                "seed",
                "checkpoint",
                "generated_vehicle_count",
                "departed_vehicle_count",
                "arrived_vehicle_count",
                "unfinished_vehicle_count",
                "completion_rate",
                "final_network_vehicle_count",
            ):
                self.assertIn(required, header)

    def test_fixed_time_installs_static_program_distinct_from_actuated_network(self) -> None:
        from hongdae_baseline.signal import TLSDefinition

        definition = TLSDefinition.from_network(self.config.network, self.config.tls_id)
        self.assertEqual(definition.native_type, "actuated")
        connection = _FixedConnection()
        runtime = _install_fixed_time(connection, definition)
        self.assertEqual(runtime["type"], "static")
        self.assertEqual(connection.trafficlight.logic.type, 0)
        self.assertEqual(
            [phase.duration for phase in connection.trafficlight.logic.phases],
            [phase.duration for phase in definition.native_phases],
        )

    @unittest.skipUnless(find_sumo_binary("sumo"), "SUMO executable is not installed")
    def test_live_sumo_reports_requested_route_after_start(self) -> None:
        configure_sumo_runtime()
        import traci

        config = replace(self.config, horizon_seconds=1)
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            route = generate_route(config.route_template, directory_path / "route.xml", 7, end_seconds=1).path
            command = build_sumo_command(
                find_sumo_binary("sumo"),
                config,
                route,
                directory_path / "tripinfo.xml",
                SeedBundle.from_master(7, "master"),
            )
            label = "route_contract_test"
            traci.start(command, label=label)
            connection = traci.getConnection(label)
            try:
                self.assertEqual(verify_loaded_route(connection, route), str(route.resolve()))
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
