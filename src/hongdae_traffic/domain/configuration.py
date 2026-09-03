from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any

from .models import SCHEMA_VERSION, _finite_range, _nonempty, _nonnegative_int, _unique_nonempty


@dataclass(frozen=True)
class LaneConfiguration:
    lane_id: str
    approach_id: str
    downstream_lane_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "downstream_lane_ids", tuple(self.downstream_lane_ids))
        _nonempty(self.lane_id, "lane_id")
        _nonempty(self.approach_id, "approach_id")
        _unique_nonempty(self.downstream_lane_ids, "downstream_lane_ids", allow_empty=False)


@dataclass(frozen=True)
class GreenPhaseConfiguration:
    phase_id: str
    tls_id: str
    phase_index: int
    signal_state: str

    def __post_init__(self) -> None:
        _nonempty(self.phase_id, "phase_id")
        _nonempty(self.tls_id, "tls_id")
        _nonnegative_int(self.phase_index, "phase_index")
        _nonempty(self.signal_state, "signal_state")
        if "y" in self.signal_state.lower() or not any(value in "Gg" for value in self.signal_state):
            raise ValueError("green phase signal_state must contain green and no yellow")


@dataclass(frozen=True)
class MovementConfiguration:
    movement_id: str
    tls_id: str
    link_index: int
    from_lane_id: str
    to_lane_id: str
    turn: str
    service_phase_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "service_phase_ids", tuple(self.service_phase_ids))
        for field_name in ("movement_id", "tls_id", "from_lane_id", "to_lane_id", "turn"):
            _nonempty(getattr(self, field_name), field_name)
        _nonnegative_int(self.link_index, "link_index")
        if self.turn not in {"right", "straight", "left", "u_turn"}:
            raise ValueError(f"Unsupported turn {self.turn!r}")
        _unique_nonempty(self.service_phase_ids, "service_phase_ids", allow_empty=False)


@dataclass(frozen=True)
class MeasurementConfiguration:
    jam_spacing_meters: float
    downstream_occupancy_aggregation: str
    step_length_seconds: float

    def __post_init__(self) -> None:
        _finite_range(self.jam_spacing_meters, "jam_spacing_meters", minimum=1e-9)
        _finite_range(self.step_length_seconds, "step_length_seconds", minimum=1e-9)
        if self.downstream_occupancy_aggregation not in {"maximum", "mean"}:
            raise ValueError("downstream_occupancy_aggregation must be 'maximum' or 'mean'")


@dataclass(frozen=True)
class IntersectionConfiguration:
    schema_version: str
    intersection_id: str
    tls_ids: tuple[str, ...]
    adjacent_intersection_ids: tuple[str, ...]
    pedestrian_crossings_supported: bool
    measurement: MeasurementConfiguration
    lanes: tuple[LaneConfiguration, ...]
    green_phases: tuple[GreenPhaseConfiguration, ...]
    movements: tuple[MovementConfiguration, ...]

    def __post_init__(self) -> None:
        for field_name in ("tls_ids", "adjacent_intersection_ids", "lanes", "green_phases", "movements"):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported configuration schema_version {self.schema_version!r}")
        _nonempty(self.intersection_id, "intersection_id")
        _unique_nonempty(self.tls_ids, "tls_ids", allow_empty=False)
        _unique_nonempty(self.adjacent_intersection_ids, "adjacent_intersection_ids")
        if self.intersection_id in self.adjacent_intersection_ids:
            raise ValueError("intersection cannot be adjacent to itself")
        if not isinstance(self.pedestrian_crossings_supported, bool):
            raise ValueError("pedestrian_crossings_supported must be boolean")
        self._validate_relations()

    def _validate_relations(self) -> None:
        lane_ids = [lane.lane_id for lane in self.lanes]
        phase_ids = [phase.phase_id for phase in self.green_phases]
        movement_ids = [movement.movement_id for movement in self.movements]
        for values, label in ((lane_ids, "lane_id"), (phase_ids, "phase_id"), (movement_ids, "movement_id")):
            if not values or len(values) != len(set(values)):
                raise ValueError(f"{label} values must be non-empty and unique")
        tls_ids = set(self.tls_ids)
        phases = {phase.phase_id: phase for phase in self.green_phases}
        lanes = {lane.lane_id: lane for lane in self.lanes}
        connections: set[tuple[str, int, str, str]] = set()
        phase_indices: set[tuple[str, int]] = set()
        for phase in self.green_phases:
            if phase.tls_id not in tls_ids:
                raise ValueError(f"phase {phase.phase_id} references unknown TLS {phase.tls_id}")
            phase_key = (phase.tls_id, phase.phase_index)
            if phase_key in phase_indices:
                raise ValueError(f"duplicate green phase index {phase_key}")
            phase_indices.add(phase_key)
        for movement in self.movements:
            if movement.tls_id not in tls_ids:
                raise ValueError(f"movement {movement.movement_id} references unknown TLS")
            if movement.from_lane_id not in lanes:
                raise ValueError(f"movement {movement.movement_id} references unknown incoming lane")
            if movement.to_lane_id not in lanes[movement.from_lane_id].downstream_lane_ids:
                raise ValueError(f"movement {movement.movement_id} destination is absent from its lane mapping")
            if not set(movement.service_phase_ids) <= set(phases):
                raise ValueError(f"movement {movement.movement_id} references unknown service phase")
            if any(phases[item].tls_id != movement.tls_id for item in movement.service_phase_ids):
                raise ValueError(f"movement {movement.movement_id} service phase belongs to another TLS")
            runtime_service_phases = {
                phase.phase_id
                for phase in self.green_phases
                if phase.tls_id == movement.tls_id
                and movement.link_index < len(phase.signal_state)
                and phase.signal_state[movement.link_index] in "Gg"
            }
            if set(movement.service_phase_ids) != runtime_service_phases:
                raise ValueError(
                    f"movement {movement.movement_id} service phases disagree with signal states"
                )
            connection = (
                movement.tls_id,
                movement.link_index,
                movement.from_lane_id,
                movement.to_lane_id,
            )
            if connection in connections:
                raise ValueError(f"duplicate movement connection {connection}")
            connections.add(connection)


def _table_list(raw: dict[str, Any], name: str) -> list[dict[str, Any]]:
    value = raw.get(name)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{name} must be an array of TOML tables")
    return value


def load_intersection_config(path: Path) -> IntersectionConfiguration:
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    measurement = raw.get("measurement")
    if not isinstance(measurement, dict):
        raise ValueError("measurement must be a TOML table")
    return IntersectionConfiguration(
        schema_version=str(raw["schema_version"]),
        intersection_id=str(raw["intersection_id"]),
        tls_ids=tuple(str(value) for value in raw["tls_ids"]),
        adjacent_intersection_ids=tuple(str(value) for value in raw["adjacent_intersection_ids"]),
        pedestrian_crossings_supported=raw["pedestrian_crossings_supported"],
        measurement=MeasurementConfiguration(
            jam_spacing_meters=measurement["jam_spacing_meters"],
            downstream_occupancy_aggregation=str(measurement["downstream_occupancy_aggregation"]),
            step_length_seconds=measurement["step_length_seconds"],
        ),
        lanes=tuple(
            LaneConfiguration(
                lane_id=str(item["lane_id"]),
                approach_id=str(item["approach_id"]),
                downstream_lane_ids=tuple(str(value) for value in item["downstream_lane_ids"]),
            )
            for item in _table_list(raw, "lanes")
        ),
        green_phases=tuple(
            GreenPhaseConfiguration(
                phase_id=str(item["phase_id"]),
                tls_id=str(item["tls_id"]),
                phase_index=item["phase_index"],
                signal_state=str(item["signal_state"]),
            )
            for item in _table_list(raw, "green_phases")
        ),
        movements=tuple(
            MovementConfiguration(
                movement_id=str(item["movement_id"]),
                tls_id=str(item["tls_id"]),
                link_index=item["link_index"],
                from_lane_id=str(item["from_lane_id"]),
                to_lane_id=str(item["to_lane_id"]),
                turn=str(item["turn"]),
                service_phase_ids=tuple(str(value) for value in item["service_phase_ids"]),
            )
            for item in _table_list(raw, "movements")
        ),
    )
