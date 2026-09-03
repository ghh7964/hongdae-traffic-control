from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import unittest

from hongdae_traffic.adapters import SUMOAdapter
from hongdae_traffic.domain import (
    GreenPhaseConfiguration,
    TrafficSnapshot,
    load_intersection_config,
)


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class _Phase:
    state: str


@dataclass
class _Logic:
    programID: str
    phases: tuple[_Phase, ...]


class _SimulationAPI:
    def __init__(self) -> None:
        self.time = 0.0
        self.departed = 0
        self.arrived = 0
        self.teleports: tuple[str, ...] = ()

    def getTime(self):
        return self.time

    def getDepartedNumber(self):
        return self.departed

    def getArrivedNumber(self):
        return self.arrived

    def getStartingTeleportIDList(self):
        return self.teleports


class _LaneAPI:
    def __init__(self, config) -> None:
        self.lengths = {lane.lane_id: 15.0 for lane in config.lanes}
        self.counts = {lane.lane_id: 0 for lane in config.lanes}
        self.halting = {lane.lane_id: 0 for lane in config.lanes}
        self.vehicle_ids = {lane.lane_id: () for lane in config.lanes}
        self.speeds = {lane.lane_id: 13.0 for lane in config.lanes}
        downstream = {item for lane in config.lanes for item in lane.downstream_lane_ids}
        self.occupancies = {lane_id: 0.0 for lane_id in downstream}

    def getLength(self, lane_id):
        return self.lengths[lane_id]

    def getLastStepVehicleNumber(self, lane_id):
        return self.counts[lane_id]

    def getLastStepHaltingNumber(self, lane_id):
        return self.halting[lane_id]

    def getLastStepVehicleIDs(self, lane_id):
        return self.vehicle_ids[lane_id]

    def getLastStepMeanSpeed(self, lane_id):
        return self.speeds[lane_id]

    def getLastStepOccupancy(self, lane_id):
        return self.occupancies[lane_id]


class _VehicleAPI:
    waits = {"veh0": 7.0, "veh1": 11.5}

    def getWaitingTime(self, vehicle_id):
        return self.waits[vehicle_id]


class _TrafficLightAPI:
    def __init__(self, config) -> None:
        self.config = config
        self.logics = {}
        self.active_phases = {}
        self.elapsed = {}
        for position, tls_id in enumerate(config.tls_ids):
            phases = [item for item in config.green_phases if item.tls_id == tls_id]
            max_index = max((item.phase_index for item in phases), default=0)
            state_length = len(phases[0].signal_state) if phases else 1
            states = ["r" * state_length for _ in range(max_index + 1)]
            for phase in phases:
                states[phase.phase_index] = phase.signal_state
            self.logics[tls_id] = _Logic("0", tuple(_Phase(state) for state in states))
            self.active_phases[tls_id] = 2 if any(item.phase_index == 2 for item in phases) else 0
            self.elapsed[tls_id] = 4.0 + 5.0 * position

    def getIDList(self):
        return self.config.tls_ids

    def getControlledLanes(self, tls_id):
        return tuple(
            item.from_lane_id for item in self.config.movements if item.tls_id == tls_id
        )

    def getControlledLinks(self, tls_id):
        movements = [item for item in self.config.movements if item.tls_id == tls_id]
        if not movements:
            return ()
        links: list[list[tuple[str, str, str]]] = [
            [] for _ in range(max(item.link_index for item in movements) + 1)
        ]
        for item in movements:
            links[item.link_index].append(
                (item.from_lane_id, item.to_lane_id, f":via_{item.link_index}")
            )
        return tuple(tuple(items) for items in links)

    def getProgram(self, tls_id):
        return "0"

    def getAllProgramLogics(self, tls_id):
        return (self.logics[tls_id],)

    def getPhase(self, tls_id):
        return self.active_phases[tls_id]

    def getRedYellowGreenState(self, tls_id):
        logic = self.logics[tls_id]
        return logic.phases[self.active_phases[tls_id]].state

    def getSpentDuration(self, tls_id):
        return self.elapsed[tls_id]


class _Connection:
    def __init__(self, config) -> None:
        self.simulation = _SimulationAPI()
        self.lane = _LaneAPI(config)
        self.vehicle = _VehicleAPI()
        self.trafficlight = _TrafficLightAPI(config)


class SUMOAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_intersection_config(ROOT / "configs/intersections/legacy_gate.toml")
        self.connection = _Connection(self.config)

    def test_collects_lane_signal_wait_and_downstream_values(self) -> None:
        first = self.config.lanes[0]
        self.connection.lane.counts[first.lane_id] = 2
        self.connection.lane.halting[first.lane_id] = 1
        self.connection.lane.vehicle_ids[first.lane_id] = ("veh0", "veh1")
        self.connection.lane.occupancies[first.downstream_lane_ids[0]] = 25.0
        self.connection.lane.occupancies[first.downstream_lane_ids[1]] = 60.0
        snapshot = SUMOAdapter(self.connection, self.config).collect()
        intersection = snapshot.intersections[0]
        signal = intersection.signal_controllers[0]
        lane = intersection.lanes[0]
        self.assertEqual(signal.active_phase, 2)
        self.assertEqual(signal.phase_elapsed_seconds, 4.0)
        self.assertEqual(lane.vehicle_count, 2)
        self.assertEqual(lane.halting_count, 1)
        self.assertEqual(lane.max_wait_seconds, 11.5)
        self.assertEqual(lane.density, 1.0)
        self.assertEqual(lane.queue_ratio, 0.5)
        self.assertEqual(lane.downstream_occupancy, 0.6)
        self.assertEqual(intersection.pedestrian_crossings, ())
        self.assertIn("pedestrian_crossings", intersection.unsupported_fields)
        empty_lane = intersection.lanes[1]
        self.assertIsNone(empty_lane.mean_speed)
        self.assertNotIn(f"lanes.{empty_lane.lane_id}.mean_speed", intersection.missing_fields)

    def test_diagnostics_are_cumulative_and_same_step_collection_is_idempotent(self) -> None:
        adapter = SUMOAdapter(self.connection, self.config)
        self.assertEqual(adapter.collect().diagnostics.departed, 0)
        self.connection.simulation.time = 1.0
        self.connection.simulation.departed = 2
        self.connection.simulation.arrived = 1
        self.connection.simulation.teleports = ("veh0",)
        first = adapter.collect()
        repeated = adapter.collect()
        self.assertEqual(first.diagnostics, repeated.diagnostics)
        self.assertEqual(first.diagnostics.departed, 2)
        self.assertEqual(first.diagnostics.throughput, 1)
        self.assertEqual(first.diagnostics.teleport, 1)

    def test_skipped_steps_mark_diagnostics_missing_instead_of_returning_partial_zero(self) -> None:
        adapter = SUMOAdapter(self.connection, self.config)
        self.connection.simulation.time = 2.0
        snapshot = adapter.collect()
        self.assertIsNone(snapshot.diagnostics.departed)
        self.assertIn("departed", snapshot.diagnostics.missing_fields)

    def test_runtime_lane_mismatch_fails_fast(self) -> None:
        self.connection.trafficlight.getControlledLanes = lambda tls_id: ("wrong_lane",)
        with self.assertRaisesRegex(RuntimeError, "Controlled lane order mismatch"):
            SUMOAdapter(self.connection, self.config)

    def test_mock_snapshot_json_round_trip_preserves_values(self) -> None:
        snapshot = SUMOAdapter(self.connection, self.config).collect()
        self.assertEqual(TrafficSnapshot.from_json(snapshot.to_json()), snapshot)

    def test_one_intersection_collects_two_tls_without_mixing_signal_state(self) -> None:
        second_phase = GreenPhaseConfiguration("second_green", "tls_2", 0, "G")
        config = replace(
            self.config,
            tls_ids=self.config.tls_ids + ("tls_2",),
            green_phases=self.config.green_phases + (second_phase,),
        )
        snapshot = SUMOAdapter(_Connection(config), config).collect()
        controllers = snapshot.intersections[0].signal_controllers
        self.assertEqual(len(controllers), 2)
        self.assertEqual(
            [(item.tls_id, item.active_phase, item.signal_state, item.phase_elapsed_seconds) for item in controllers],
            [
                ("2959081059", 2, "GGrrrrrrGGGrrrrrr", 4.0),
                ("tls_2", 0, "G", 9.0),
            ],
        )

    def test_multiple_connections_per_link_require_exact_runtime_set(self) -> None:
        first_movement = self.config.movements[0]
        extra_movement = replace(
            first_movement,
            movement_id="northeast_lane_0_second_connection",
            to_lane_id=self.config.lanes[0].downstream_lane_ids[1],
        )
        config = replace(
            self.config,
            movements=self.config.movements + (extra_movement,),
        )
        connection = _Connection(config)
        SUMOAdapter(connection, config)

        runtime_links = connection.trafficlight.getControlledLinks(config.tls_ids[0])
        connection.trafficlight.getControlledLinks = lambda tls_id: (
            runtime_links[0][:1], *runtime_links[1:]
        )
        with self.assertRaisesRegex(RuntimeError, "connection-set mismatch"):
            SUMOAdapter(connection, config)

    def _retarget(self, intersection_id, tls_id, adjacent=(), step_length=1.0):
        phases = tuple(replace(item, tls_id=tls_id) for item in self.config.green_phases)
        movements = tuple(replace(item, tls_id=tls_id) for item in self.config.movements)
        measurement = replace(
            self.config.measurement,
            step_length_seconds=step_length,
        )
        return replace(
            self.config,
            intersection_id=intersection_id,
            tls_ids=(tls_id,),
            adjacent_intersection_ids=adjacent,
            green_phases=phases,
            movements=movements,
            measurement=measurement,
        )

    def test_multi_configuration_relationship_validation(self) -> None:
        with self.assertRaisesRegex(ValueError, "unique IDs"):
            SUMOAdapter(self.connection, (self.config, self.config), validate_runtime=False)

        duplicate_tls = replace(self.config, intersection_id="duplicate_tls")
        with self.assertRaisesRegex(ValueError, "multiple configured intersections"):
            SUMOAdapter(self.connection, (self.config, duplicate_tls), validate_runtime=False)

        unknown_neighbor = replace(self.config, adjacent_intersection_ids=("future",))
        with self.assertRaisesRegex(ValueError, "unconfigured neighbors"):
            SUMOAdapter(self.connection, unknown_neighbor, validate_runtime=False)

        first = replace(self.config, adjacent_intersection_ids=("second",))
        asymmetric = self._retarget("second", "tls_2")
        with self.assertRaisesRegex(ValueError, "asymmetric adjacency"):
            SUMOAdapter(self.connection, (first, asymmetric), validate_runtime=False)

        second = self._retarget("second", "tls_2", adjacent=("legacy_gate",))
        adapter = SUMOAdapter(self.connection, (first, second), validate_runtime=False)
        self.assertEqual(len(adapter.configurations), 2)

        different_step = self._retarget("second", "tls_2", step_length=0.5)
        with self.assertRaisesRegex(ValueError, "same simulation step length"):
            SUMOAdapter(self.connection, (self.config, different_step), validate_runtime=False)


if __name__ == "__main__":
    unittest.main()
