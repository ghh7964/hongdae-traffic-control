from __future__ import annotations

import math
from statistics import fmean
from typing import Any, Sequence

from hongdae_traffic.domain import (
    SCHEMA_VERSION,
    IntersectionConfiguration,
    IntersectionSnapshot,
    SignalControllerSnapshot,
    TrafficDiagnostics,
    TrafficSnapshot,
    TrafficSource,
    VehicleLaneSnapshot,
)


class SUMOAdapter:
    """Translate an injected TraCI-like connection into pure domain snapshots.

    The adapter never returns a SUMO or TraCI object. For complete cumulative
    diagnostics it must be created at simulation time zero and collected once per
    simulation step. Repeated collection within one step is idempotent.
    """

    def __init__(
        self,
        connection: Any,
        configuration: IntersectionConfiguration | Sequence[IntersectionConfiguration],
        *,
        validate_runtime: bool = True,
    ) -> None:
        configurations = (
            (configuration,)
            if isinstance(configuration, IntersectionConfiguration)
            else tuple(configuration)
        )
        if not configurations:
            raise ValueError("SUMOAdapter requires at least one intersection configuration")
        intersection_ids = [item.intersection_id for item in configurations]
        if len(intersection_ids) != len(set(intersection_ids)):
            raise ValueError("intersection configurations must have unique IDs")
        tls_ids = [tls_id for item in configurations for tls_id in item.tls_ids]
        if len(tls_ids) != len(set(tls_ids)):
            raise ValueError("a TLS must not belong to multiple configured intersections")
        configurations_by_id = {item.intersection_id: item for item in configurations}
        for item in configurations:
            unknown_neighbors = set(item.adjacent_intersection_ids) - set(configurations_by_id)
            if unknown_neighbors:
                raise ValueError(
                    f"intersection {item.intersection_id} references unconfigured neighbors: "
                    f"{sorted(unknown_neighbors)}"
                )
            for neighbor_id in item.adjacent_intersection_ids:
                neighbor = configurations_by_id[neighbor_id]
                if item.intersection_id not in neighbor.adjacent_intersection_ids:
                    raise ValueError(
                        f"asymmetric adjacency: {item.intersection_id} -> {neighbor_id}"
                    )
        step_lengths = {item.measurement.step_length_seconds for item in configurations}
        if len(step_lengths) != 1:
            raise ValueError("all intersections must use the same simulation step length")
        self._connection = connection
        self.configurations = configurations
        self._step_length_seconds = configurations[0].measurement.step_length_seconds
        if validate_runtime:
            self.validate_runtime()
        self._lane_lengths = {
            lane.lane_id: float(connection.lane.getLength(lane.lane_id))
            for item in configurations
            for lane in item.lanes
        }
        if any(not math.isfinite(value) or value <= 0 for value in self._lane_lengths.values()):
            raise RuntimeError("SUMO reported a non-positive or non-finite incoming lane length")
        current_step = self._step_from_time(float(connection.simulation.getTime()))
        self._last_accounted_step = current_step
        self._diagnostics_complete = current_step == 0
        self._departed = 0
        self._arrived = 0
        self._teleport = 0

    def _step_from_time(self, timestamp: float) -> int:
        step = timestamp / self._step_length_seconds
        rounded = round(step)
        if not math.isclose(step, rounded, rel_tol=0.0, abs_tol=1e-7):
            raise RuntimeError(f"SUMO time {timestamp} is not aligned to configured step length")
        return int(rounded)

    def validate_runtime(self) -> None:
        tls_api = self._connection.trafficlight
        runtime_tls = set(tls_api.getIDList())
        missing_tls = {
            tls_id
            for item in self.configurations
            for tls_id in item.tls_ids
            if tls_id not in runtime_tls
        }
        if missing_tls:
            raise RuntimeError(f"Configured TLS absent from SUMO: {sorted(missing_tls)}")

        for configuration in self.configurations:
            runtime_lanes: list[str] = []
            for tls_id in configuration.tls_ids:
                for lane_id in tls_api.getControlledLanes(tls_id):
                    if lane_id not in runtime_lanes:
                        runtime_lanes.append(lane_id)
            configured_lanes = [lane.lane_id for lane in configuration.lanes]
            if runtime_lanes != configured_lanes:
                raise RuntimeError(
                    f"Controlled lane order mismatch for {configuration.intersection_id}: "
                    f"configured={configured_lanes}, runtime={runtime_lanes}"
                )

            for tls_id in configuration.tls_ids:
                controlled_links = tls_api.getControlledLinks(tls_id)
                configured: dict[int, set[tuple[str, str]]] = {}
                for movement in configuration.movements:
                    if movement.tls_id == tls_id:
                        configured.setdefault(movement.link_index, set()).add(
                            (movement.from_lane_id, movement.to_lane_id)
                        )
                if set(configured) != set(range(len(controlled_links))):
                    raise RuntimeError(f"Configured link indices do not cover runtime TLS {tls_id}")
                for link_index, links in enumerate(controlled_links):
                    runtime_pairs = {(item[0], item[1]) for item in links}
                    if configured[link_index] != runtime_pairs:
                        raise RuntimeError(
                            f"Controlled link {tls_id}:{link_index} connection-set mismatch: "
                            f"configured={sorted(configured[link_index])}, "
                            f"runtime={sorted(runtime_pairs)}"
                        )

                program_id = tls_api.getProgram(tls_id)
                logic = next(
                    (
                        item
                        for item in tls_api.getAllProgramLogics(tls_id)
                        if item.programID == program_id
                    ),
                    None,
                )
                if logic is None:
                    raise RuntimeError(f"Active TLS program {tls_id}:{program_id} is unavailable")
                runtime_states = tuple(phase.state for phase in logic.phases)
                for phase in configuration.green_phases:
                    if phase.tls_id != tls_id:
                        continue
                    if (
                        phase.phase_index >= len(runtime_states)
                        or runtime_states[phase.phase_index] != phase.signal_state
                    ):
                        raise RuntimeError(
                            f"Configured green phase {phase.phase_id} differs from SUMO program"
                        )

    def _update_diagnostics(self, step: int) -> TrafficDiagnostics:
        if step < self._last_accounted_step:
            raise RuntimeError("SUMO simulation time moved backwards; create or reset the adapter")
        if step > self._last_accounted_step:
            if step != self._last_accounted_step + 1:
                self._diagnostics_complete = False
            simulation = self._connection.simulation
            self._departed += int(simulation.getDepartedNumber())
            self._arrived += int(simulation.getArrivedNumber())
            self._teleport += len(simulation.getStartingTeleportIDList())
            self._last_accounted_step = step
        if not self._diagnostics_complete:
            return TrafficDiagnostics.missing()
        return TrafficDiagnostics(
            departed=self._departed,
            arrived=self._arrived,
            throughput=self._arrived,
            teleport=self._teleport,
        )

    def _downstream_occupancy(
        self,
        downstream_lane_ids: tuple[str, ...],
        configuration: IntersectionConfiguration,
    ) -> float:
        values = [
            min(1.0, max(0.0, float(self._connection.lane.getLastStepOccupancy(lane_id)) / 100.0))
            for lane_id in downstream_lane_ids
        ]
        if configuration.measurement.downstream_occupancy_aggregation == "maximum":
            return max(values)
        return fmean(values)

    def _lane_snapshot(
        self,
        lane_configuration: Any,
        configuration: IntersectionConfiguration,
    ) -> VehicleLaneSnapshot:
        lane_api = self._connection.lane
        vehicle_api = self._connection.vehicle
        lane_id = lane_configuration.lane_id
        vehicle_count = int(lane_api.getLastStepVehicleNumber(lane_id))
        halting_count = int(lane_api.getLastStepHaltingNumber(lane_id))
        capacity = self._lane_lengths[lane_id] / configuration.measurement.jam_spacing_meters
        vehicle_ids = tuple(lane_api.getLastStepVehicleIDs(lane_id))
        waits = [float(vehicle_api.getWaitingTime(vehicle_id)) for vehicle_id in vehicle_ids]
        mean_speed = float(lane_api.getLastStepMeanSpeed(lane_id))
        if vehicle_count == 0 or not math.isfinite(mean_speed) or mean_speed < 0:
            mean_speed = None
        return VehicleLaneSnapshot(
            lane_id=lane_id,
            approach_id=lane_configuration.approach_id,
            vehicle_count=vehicle_count,
            halting_count=halting_count,
            density=min(1.0, vehicle_count / capacity),
            queue_ratio=min(1.0, halting_count / capacity),
            max_wait_seconds=max(waits, default=0.0),
            mean_speed=mean_speed,
            downstream_occupancy=self._downstream_occupancy(
                lane_configuration.downstream_lane_ids, configuration
            ),
            confidence=1.0,
        )

    def _intersection_snapshot(
        self, configuration: IntersectionConfiguration
    ) -> IntersectionSnapshot:
        tls_api = self._connection.trafficlight
        lanes = tuple(self._lane_snapshot(lane, configuration) for lane in configuration.lanes)
        signal_controllers = tuple(
            SignalControllerSnapshot(
                tls_id=tls_id,
                active_phase=int(tls_api.getPhase(tls_id)),
                signal_state=str(tls_api.getRedYellowGreenState(tls_id)),
                phase_elapsed_seconds=float(tls_api.getSpentDuration(tls_id)),
                confidence=1.0,
                missing_fields=(),
            )
            for tls_id in configuration.tls_ids
        )
        return IntersectionSnapshot(
            intersection_id=configuration.intersection_id,
            signal_controllers=signal_controllers,
            lanes=lanes,
            pedestrian_crossings=(),
            confidence=1.0,
            missing_fields=(),
            unsupported_fields=("pedestrian_crossings",),
        )

    def collect(self) -> TrafficSnapshot:
        timestamp = float(self._connection.simulation.getTime())
        step = self._step_from_time(timestamp)
        return TrafficSnapshot(
            schema_version=SCHEMA_VERSION,
            timestamp_seconds=timestamp,
            sequence_index=step,
            source=TrafficSource.SUMO,
            intersections=tuple(self._intersection_snapshot(item) for item in self.configurations),
            diagnostics=self._update_diagnostics(step),
        )
