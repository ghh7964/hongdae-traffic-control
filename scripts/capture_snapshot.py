#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hongdae_baseline.assets import configure_sumo_runtime, find_sumo_binary
from hongdae_baseline.config import load_config
from hongdae_baseline.evaluator import build_sumo_command
from hongdae_baseline.route import generate_route
from hongdae_baseline.seeds import SeedBundle
from hongdae_traffic.adapters import SUMOAdapter
from hongdae_traffic.domain import load_intersection_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture one headless corrected-baseline TrafficSnapshot")
    parser.add_argument("--seed", type=int, default=101)
    parser.add_argument("--step", type=int, default=40)
    parser.add_argument("--route", type=Path)
    parser.add_argument("--output", type=Path, help="JSON path; stdout when omitted")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.step < 0:
        raise ValueError("--step must be non-negative")
    configure_sumo_runtime()
    binary = find_sumo_binary("sumo")
    if binary is None:
        raise RuntimeError("SUMO executable is not installed or discoverable")
    config = replace(load_config("corrected_baseline", ROOT), horizon_seconds=max(1, args.step))
    intersection_config = load_intersection_config(ROOT / "configs/intersections/legacy_gate.toml")
    seed = SeedBundle.from_master(args.seed, config.sumo_seed_mode)

    with tempfile.TemporaryDirectory(prefix="hongdae_snapshot_") as directory:
        temporary = Path(directory)
        route = args.route.resolve() if args.route else generate_route(
            config.route_template,
            temporary / "route.rou.xml",
            args.seed,
            config.demand_end_seconds,
            config.vehicle_period_seconds,
        ).path
        command = build_sumo_command(binary, config, route, temporary / "tripinfo.xml", seed)
        import traci

        label = f"snapshot_{uuid.uuid4().hex}"
        traci.start(command, label=label)
        connection = traci.getConnection(label)
        try:
            adapter = SUMOAdapter(connection, intersection_config)
            snapshot = adapter.collect()
            for _ in range(args.step):
                connection.simulationStep()
                snapshot = adapter.collect()
        finally:
            connection.close()

    payload = snapshot.to_json(indent=2) + "\n"
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
        print(output)
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

