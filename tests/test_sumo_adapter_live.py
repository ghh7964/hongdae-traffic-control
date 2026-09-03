from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import uuid

from hongdae_baseline.assets import AssetManifest, configure_sumo_runtime, find_sumo_binary
from hongdae_baseline.config import load_config
from hongdae_baseline.evaluator import build_sumo_command, evaluate_controller
from hongdae_baseline.route import generate_route
from hongdae_baseline.seeds import SeedBundle
from hongdae_traffic.adapters import SUMOAdapter
from hongdae_traffic.domain import TrafficSnapshot, load_intersection_config


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(find_sumo_binary("sumo"), "SUMO executable is not installed")
class LiveSUMOAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        configure_sumo_runtime()
        cls.evaluation_config = replace(load_config("corrected_baseline", ROOT), horizon_seconds=40)
        cls.intersection_config = load_intersection_config(
            ROOT / "configs/intersections/legacy_gate.toml"
        )

    def _run_to_step(self, route: Path, output: Path, target_step: int):
        import traci

        output.mkdir(parents=True, exist_ok=True)
        seed = SeedBundle.from_master(101, "master")
        command = build_sumo_command(
            find_sumo_binary("sumo"),
            self.evaluation_config,
            route,
            output / "tripinfo.xml",
            seed,
        )
        label = f"snapshot_live_{uuid.uuid4().hex}"
        traci.start(command, label=label)
        connection = traci.getConnection(label)
        try:
            adapter = SUMOAdapter(connection, self.intersection_config)
            departed = arrived = teleports = 0
            snapshot = adapter.collect()
            for _ in range(target_step):
                connection.simulationStep()
                departed += int(connection.simulation.getDepartedNumber())
                arrived += int(connection.simulation.getArrivedNumber())
                teleports += len(connection.simulation.getStartingTeleportIDList())
                snapshot = adapter.collect()
            self.assertEqual(snapshot.diagnostics.departed, departed)
            self.assertEqual(snapshot.diagnostics.arrived, arrived)
            self.assertEqual(snapshot.diagnostics.throughput, arrived)
            self.assertEqual(snapshot.diagnostics.teleport, teleports)
            return snapshot
        finally:
            connection.close()

    def test_corrected_baseline_live_contract_round_trip_and_determinism(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            route = generate_route(
                self.evaluation_config.route_template,
                output / "route.rou.xml",
                master_seed=101,
                end_seconds=self.evaluation_config.demand_end_seconds,
                period_seconds=self.evaluation_config.vehicle_period_seconds,
            ).path
            first = self._run_to_step(route, output / "run_1", target_step=40)
            second = self._run_to_step(route, output / "run_2", target_step=40)
            evaluator_record, _ = evaluate_controller(
                self.evaluation_config,
                "ACTUATED",
                route,
                SeedBundle.from_master(101, "master"),
                output / "evaluator",
                AssetManifest(self.evaluation_config.asset_manifest, ROOT),
            )

        configured_lanes = tuple(item.lane_id for item in self.intersection_config.lanes)
        signal_controllers = first.intersections[0].signal_controllers
        self.assertEqual(
            tuple(item.tls_id for item in signal_controllers),
            self.intersection_config.tls_ids,
        )
        self.assertEqual(tuple(item.lane_id for item in first.intersections[0].lanes), configured_lanes)
        self.assertIsNotNone(signal_controllers[0].active_phase)
        self.assertTrue(signal_controllers[0].signal_state)
        self.assertTrue(all(item.vehicle_count is not None for item in first.intersections[0].lanes))
        self.assertTrue(all(item.halting_count is not None for item in first.intersections[0].lanes))
        self.assertTrue(all(item.max_wait_seconds is not None for item in first.intersections[0].lanes))
        self.assertTrue(all(item.downstream_occupancy is not None for item in first.intersections[0].lanes))
        self.assertEqual(TrafficSnapshot.from_json(first.to_json()), first)
        self.assertEqual(first.comparable_domain_dict(), second.comparable_domain_dict())
        self.assertEqual(first.diagnostics.departed, evaluator_record["departed_vehicle_count"])
        self.assertEqual(first.diagnostics.arrived, evaluator_record["arrived_vehicle_count"])
        self.assertEqual(first.diagnostics.throughput, evaluator_record["throughput"])
        self.assertEqual(first.diagnostics.teleport, evaluator_record["teleport_count"])


if __name__ == "__main__":
    unittest.main()
