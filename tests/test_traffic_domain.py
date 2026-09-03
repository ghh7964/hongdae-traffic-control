from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest

from hongdae_traffic.domain import (
    SCHEMA_VERSION,
    IntersectionSnapshot,
    SignalControllerSnapshot,
    TrafficDiagnostics,
    TrafficSnapshot,
    VehicleLaneSnapshot,
    load_intersection_config,
)


ROOT = Path(__file__).resolve().parents[1]


def example_snapshot() -> TrafficSnapshot:
    lane = VehicleLaneSnapshot(
        lane_id="lane_0",
        approach_id="north",
        vehicle_count=2,
        halting_count=1,
        density=0.2,
        queue_ratio=0.1,
        max_wait_seconds=4.5,
        mean_speed=3.0,
        downstream_occupancy=0.25,
        confidence=1.0,
    )
    intersection = IntersectionSnapshot(
        intersection_id="gate",
        signal_controllers=(SignalControllerSnapshot("tls", 2, "GGr", 3.0, 1.0),),
        lanes=(lane,),
        pedestrian_crossings=(),
        confidence=1.0,
        unsupported_fields=("pedestrian_crossings",),
    )
    return TrafficSnapshot(
        schema_version=SCHEMA_VERSION,
        timestamp_seconds=10.0,
        sequence_index=10,
        source="sumo",
        intersections=(intersection,),
        diagnostics=TrafficDiagnostics(5, 3, 3, 0),
    )


class TrafficDomainTests(unittest.TestCase):
    def test_json_round_trip_is_lossless_and_canonical(self) -> None:
        snapshot = example_snapshot()
        restored = TrafficSnapshot.from_json(snapshot.to_json())
        self.assertEqual(restored, snapshot)
        self.assertEqual(json.loads(restored.to_json()), snapshot.to_dict())

    def test_deterministic_comparison_excludes_only_timestamp(self) -> None:
        first = example_snapshot()
        later_metadata = replace(first, timestamp_seconds=123.0)
        self.assertNotEqual(first.to_dict(), later_metadata.to_dict())
        self.assertEqual(first.comparable_domain_dict(), later_metadata.comparable_domain_dict())

    def test_validation_rejects_invalid_ranges_and_counts(self) -> None:
        lane = example_snapshot().intersections[0].lanes[0]
        with self.assertRaisesRegex(ValueError, "density"):
            replace(lane, density=1.01)
        with self.assertRaisesRegex(ValueError, "halting_count"):
            replace(lane, vehicle_count=1, halting_count=2)
        with self.assertRaisesRegex(ValueError, "throughput"):
            TrafficDiagnostics(2, 1, 0, 0)
        with self.assertRaisesRegex(ValueError, "schema_version"):
            replace(example_snapshot(), schema_version="2.0.0")

    def test_missing_diagnostics_are_null_not_zero(self) -> None:
        diagnostics = TrafficDiagnostics.unsupported()
        self.assertIsNone(diagnostics.departed)
        self.assertEqual(
            diagnostics.unsupported_fields,
            ("departed", "arrived", "throughput", "teleport"),
        )
        with self.assertRaisesRegex(ValueError, "unavailable reason"):
            TrafficDiagnostics(None, 0, 0, 0)

    def test_vision_snapshot_uses_source_neutral_sequence_without_simulation_step(self) -> None:
        snapshot = replace(
            example_snapshot(),
            source="vision",
            sequence_index=240,
            timestamp_seconds=8.0,
            diagnostics=TrafficDiagnostics.unsupported(),
        )
        data = snapshot.to_dict()
        self.assertEqual(data["sequence_index"], 240)
        self.assertNotIn("simulation_step", data)
        self.assertEqual(TrafficSnapshot.from_json(snapshot.to_json()), snapshot)

    def test_one_intersection_can_hold_two_independent_signal_controllers(self) -> None:
        snapshot = example_snapshot()
        first = snapshot.intersections[0]
        second_controller = SignalControllerSnapshot("tls_2", 0, "rrGG", 9.5, 0.8)
        intersection = replace(
            first,
            signal_controllers=first.signal_controllers + (second_controller,),
        )
        restored = TrafficSnapshot.from_json(
            replace(snapshot, intersections=(intersection,)).to_json()
        )
        controllers = restored.intersections[0].signal_controllers
        self.assertEqual(
            [(item.tls_id, item.active_phase, item.signal_state) for item in controllers],
            [("tls", 2, "GGr"), ("tls_2", 0, "rrGG")],
        )

    def test_legacy_configuration_keeps_lane_and_movement_layers_separate(self) -> None:
        config = load_intersection_config(ROOT / "configs/intersections/legacy_gate.toml")
        self.assertEqual(config.schema_version, SCHEMA_VERSION)
        self.assertEqual(len(config.lanes), 6)
        self.assertEqual(len(config.movements), 17)
        southeast = [item for item in config.movements if item.from_lane_id == "-168874251#0_0"]
        self.assertEqual(len(southeast), 4)
        self.assertEqual({item.turn for item in southeast}, {"right", "straight", "left", "u_turn"})

    def test_domain_package_does_not_import_integration_frameworks(self) -> None:
        domain_root = ROOT / "src/hongdae_traffic/domain"
        content = "\n".join(path.read_text(encoding="utf-8") for path in domain_root.glob("*.py"))
        for forbidden in ("traci", "stable_baselines3", "sumo_rl", "ultralytics"):
            self.assertNotIn(f"import {forbidden}", content.lower())


if __name__ == "__main__":
    unittest.main()
