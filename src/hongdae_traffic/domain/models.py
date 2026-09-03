from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import math
from typing import Any, Mapping


SCHEMA_VERSION = "0.1.0"


class TrafficSource(StrEnum):
    SUMO = "sumo"
    VISION = "vision"


def _nonempty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _nonnegative_int(value: int | None, field_name: str) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise ValueError(f"{field_name} must be a non-negative integer or null")


def _finite_range(
    value: float | int | None,
    field_name: str,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number or null")
    if value < minimum or (maximum is not None and value > maximum):
        suffix = f"[{minimum}, {maximum}]" if maximum is not None else f">= {minimum}"
        raise ValueError(f"{field_name} must be {suffix}")


def _unique_nonempty(values: tuple[str, ...], field_name: str, allow_empty: bool = True) -> None:
    if not allow_empty and not values:
        raise ValueError(f"{field_name} must not be empty")
    for value in values:
        _nonempty(value, field_name)
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must not contain duplicates")


def _require_keys(data: Mapping[str, Any], expected: set[str], type_name: str) -> None:
    actual = set(data)
    if actual != expected:
        raise ValueError(
            f"{type_name} keys differ: missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )


@dataclass(frozen=True)
class VehicleLaneSnapshot:
    """One physical incoming lane; ratios and confidence are within [0, 1].

    Distances are not embedded in this record. ``mean_speed`` is m/s and
    ``max_wait_seconds`` is seconds. Nullable fields are unavailable/not
    applicable and must never be replaced with a fabricated zero.
    """

    lane_id: str
    approach_id: str
    vehicle_count: int | None
    halting_count: int | None
    density: float | None
    queue_ratio: float | None
    max_wait_seconds: float | None
    mean_speed: float | None
    downstream_occupancy: float | None
    confidence: float | None

    def __post_init__(self) -> None:
        _nonempty(self.lane_id, "lane_id")
        _nonempty(self.approach_id, "approach_id")
        _nonnegative_int(self.vehicle_count, "vehicle_count")
        _nonnegative_int(self.halting_count, "halting_count")
        if (
            self.vehicle_count is not None
            and self.halting_count is not None
            and self.halting_count > self.vehicle_count
        ):
            raise ValueError("halting_count must not exceed vehicle_count")
        for field_name in ("density", "queue_ratio", "downstream_occupancy", "confidence"):
            _finite_range(getattr(self, field_name), field_name, maximum=1.0)
        _finite_range(self.max_wait_seconds, "max_wait_seconds")
        _finite_range(self.mean_speed, "mean_speed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane_id": self.lane_id,
            "approach_id": self.approach_id,
            "vehicle_count": self.vehicle_count,
            "halting_count": self.halting_count,
            "density": self.density,
            "queue_ratio": self.queue_ratio,
            "max_wait_seconds": self.max_wait_seconds,
            "mean_speed": self.mean_speed,
            "downstream_occupancy": self.downstream_occupancy,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VehicleLaneSnapshot":
        expected = {
            "lane_id", "approach_id", "vehicle_count", "halting_count", "density",
            "queue_ratio", "max_wait_seconds", "mean_speed", "downstream_occupancy", "confidence",
        }
        _require_keys(data, expected, cls.__name__)
        return cls(**{key: data[key] for key in expected})


@dataclass(frozen=True)
class PedestrianCrossingSnapshot:
    """Crossing state. Nullable values explicitly represent unsupported/missing data."""

    crossing_id: str
    waiting_count: int | None
    max_wait_seconds: float | None
    signal_permitted: bool | None
    confidence: float | None

    def __post_init__(self) -> None:
        _nonempty(self.crossing_id, "crossing_id")
        _nonnegative_int(self.waiting_count, "waiting_count")
        _finite_range(self.max_wait_seconds, "max_wait_seconds")
        if self.signal_permitted is not None and not isinstance(self.signal_permitted, bool):
            raise ValueError("signal_permitted must be boolean or null")
        _finite_range(self.confidence, "confidence", maximum=1.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "crossing_id": self.crossing_id,
            "waiting_count": self.waiting_count,
            "max_wait_seconds": self.max_wait_seconds,
            "signal_permitted": self.signal_permitted,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PedestrianCrossingSnapshot":
        expected = {"crossing_id", "waiting_count", "max_wait_seconds", "signal_permitted", "confidence"}
        _require_keys(data, expected, cls.__name__)
        return cls(**{key: data[key] for key in expected})


@dataclass(frozen=True)
class SignalControllerSnapshot:
    """Source-neutral state for one signal controller within an intersection."""

    tls_id: str
    active_phase: int | None
    signal_state: str | None
    phase_elapsed_seconds: float | None
    confidence: float | None
    missing_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "missing_fields", tuple(self.missing_fields))
        _nonempty(self.tls_id, "tls_id")
        _nonnegative_int(self.active_phase, "active_phase")
        if self.signal_state is not None:
            _nonempty(self.signal_state, "signal_state")
        _finite_range(self.phase_elapsed_seconds, "phase_elapsed_seconds")
        _finite_range(self.confidence, "confidence", maximum=1.0)
        _unique_nonempty(self.missing_fields, "missing_fields")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tls_id": self.tls_id,
            "active_phase": self.active_phase,
            "signal_state": self.signal_state,
            "phase_elapsed_seconds": self.phase_elapsed_seconds,
            "confidence": self.confidence,
            "missing_fields": list(self.missing_fields),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SignalControllerSnapshot":
        expected = {
            "tls_id", "active_phase", "signal_state", "phase_elapsed_seconds",
            "confidence", "missing_fields",
        }
        _require_keys(data, expected, cls.__name__)
        return cls(
            tls_id=data["tls_id"],
            active_phase=data["active_phase"],
            signal_state=data["signal_state"],
            phase_elapsed_seconds=data["phase_elapsed_seconds"],
            confidence=data["confidence"],
            missing_fields=tuple(data["missing_fields"]),
        )


@dataclass(frozen=True)
class IntersectionSnapshot:
    intersection_id: str
    signal_controllers: tuple[SignalControllerSnapshot, ...]
    lanes: tuple[VehicleLaneSnapshot, ...]
    pedestrian_crossings: tuple[PedestrianCrossingSnapshot, ...]
    confidence: float | None
    missing_fields: tuple[str, ...] = ()
    unsupported_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "signal_controllers", tuple(self.signal_controllers))
        object.__setattr__(self, "lanes", tuple(self.lanes))
        object.__setattr__(self, "pedestrian_crossings", tuple(self.pedestrian_crossings))
        object.__setattr__(self, "missing_fields", tuple(self.missing_fields))
        object.__setattr__(self, "unsupported_fields", tuple(self.unsupported_fields))
        _nonempty(self.intersection_id, "intersection_id")
        if not self.signal_controllers:
            raise ValueError("signal_controllers must not be empty")
        _finite_range(self.confidence, "confidence", maximum=1.0)
        _unique_nonempty(self.missing_fields, "missing_fields")
        _unique_nonempty(self.unsupported_fields, "unsupported_fields")
        if set(self.missing_fields) & set(self.unsupported_fields):
            raise ValueError("a field cannot be both missing and unsupported")
        tls_ids = [controller.tls_id for controller in self.signal_controllers]
        if len(tls_ids) != len(set(tls_ids)):
            raise ValueError("signal_controllers must have unique tls_id values")
        lane_ids = [lane.lane_id for lane in self.lanes]
        if len(lane_ids) != len(set(lane_ids)):
            raise ValueError("lanes must have unique lane_id values")
        crossing_ids = [crossing.crossing_id for crossing in self.pedestrian_crossings]
        if len(crossing_ids) != len(set(crossing_ids)):
            raise ValueError("pedestrian_crossings must have unique crossing_id values")

    def to_dict(self) -> dict[str, Any]:
        return {
            "intersection_id": self.intersection_id,
            "signal_controllers": [item.to_dict() for item in self.signal_controllers],
            "lanes": [lane.to_dict() for lane in self.lanes],
            "pedestrian_crossings": [crossing.to_dict() for crossing in self.pedestrian_crossings],
            "confidence": self.confidence,
            "missing_fields": list(self.missing_fields),
            "unsupported_fields": list(self.unsupported_fields),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "IntersectionSnapshot":
        expected = {
            "intersection_id", "signal_controllers", "lanes", "pedestrian_crossings",
            "confidence", "missing_fields", "unsupported_fields",
        }
        _require_keys(data, expected, cls.__name__)
        return cls(
            intersection_id=data["intersection_id"],
            signal_controllers=tuple(
                SignalControllerSnapshot.from_dict(item) for item in data["signal_controllers"]
            ),
            lanes=tuple(VehicleLaneSnapshot.from_dict(item) for item in data["lanes"]),
            pedestrian_crossings=tuple(
                PedestrianCrossingSnapshot.from_dict(item) for item in data["pedestrian_crossings"]
            ),
            confidence=data["confidence"],
            missing_fields=tuple(data["missing_fields"]),
            unsupported_fields=tuple(data["unsupported_fields"]),
        )


@dataclass(frozen=True)
class TrafficDiagnostics:
    """Cumulative evaluation counters, intentionally separate from control state."""

    departed: int | None
    arrived: int | None
    throughput: int | None
    teleport: int | None
    missing_fields: tuple[str, ...] = ()
    unsupported_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "missing_fields", tuple(self.missing_fields))
        object.__setattr__(self, "unsupported_fields", tuple(self.unsupported_fields))
        for field_name in ("departed", "arrived", "throughput", "teleport"):
            _nonnegative_int(getattr(self, field_name), field_name)
        _unique_nonempty(self.missing_fields, "missing_fields")
        _unique_nonempty(self.unsupported_fields, "unsupported_fields")
        unavailable = set(self.missing_fields) | set(self.unsupported_fields)
        if set(self.missing_fields) & set(self.unsupported_fields):
            raise ValueError("a diagnostic cannot be both missing and unsupported")
        for field_name in ("departed", "arrived", "throughput", "teleport"):
            is_missing = getattr(self, field_name) is None
            if is_missing != (field_name in unavailable):
                raise ValueError(f"{field_name} null state must have an unavailable reason")
        if self.throughput is not None and self.arrived is not None and self.throughput != self.arrived:
            raise ValueError("throughput must equal arrived")
        if self.departed is not None and self.arrived is not None and self.arrived > self.departed:
            raise ValueError("arrived must not exceed departed")

    @classmethod
    def unsupported(cls) -> "TrafficDiagnostics":
        fields = ("departed", "arrived", "throughput", "teleport")
        return cls(None, None, None, None, unsupported_fields=fields)

    @classmethod
    def missing(cls) -> "TrafficDiagnostics":
        fields = ("departed", "arrived", "throughput", "teleport")
        return cls(None, None, None, None, missing_fields=fields)

    def to_dict(self) -> dict[str, Any]:
        return {
            "departed": self.departed,
            "arrived": self.arrived,
            "throughput": self.throughput,
            "teleport": self.teleport,
            "missing_fields": list(self.missing_fields),
            "unsupported_fields": list(self.unsupported_fields),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TrafficDiagnostics":
        expected = {
            "departed", "arrived", "throughput", "teleport",
            "missing_fields", "unsupported_fields",
        }
        _require_keys(data, expected, cls.__name__)
        return cls(
            departed=data["departed"],
            arrived=data["arrived"],
            throughput=data["throughput"],
            teleport=data["teleport"],
            missing_fields=tuple(data["missing_fields"]),
            unsupported_fields=tuple(data["unsupported_fields"]),
        )


@dataclass(frozen=True)
class TrafficSnapshot:
    schema_version: str
    timestamp_seconds: float
    sequence_index: int
    source: TrafficSource
    intersections: tuple[IntersectionSnapshot, ...]
    diagnostics: TrafficDiagnostics

    def __post_init__(self) -> None:
        object.__setattr__(self, "intersections", tuple(self.intersections))
        if isinstance(self.source, str):
            try:
                object.__setattr__(self, "source", TrafficSource(self.source))
            except ValueError as exc:
                raise ValueError("source must be 'sumo' or 'vision'") from exc
        if not isinstance(self.source, TrafficSource):
            raise ValueError("source must be 'sumo' or 'vision'")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported schema_version {self.schema_version!r}; expected {SCHEMA_VERSION!r}")
        _finite_range(self.timestamp_seconds, "timestamp_seconds")
        _nonnegative_int(self.sequence_index, "sequence_index")
        if not self.intersections:
            raise ValueError("intersections must not be empty")
        intersection_ids = [item.intersection_id for item in self.intersections]
        if len(intersection_ids) != len(set(intersection_ids)):
            raise ValueError("intersections must have unique intersection_id values")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "timestamp_seconds": self.timestamp_seconds,
            "sequence_index": self.sequence_index,
            "source": self.source.value,
            "intersections": [item.to_dict() for item in self.intersections],
            "diagnostics": self.diagnostics.to_dict(),
        }

    def comparable_domain_dict(self) -> dict[str, Any]:
        """Content used for deterministic replay comparisons.

        ``timestamp_seconds`` is metadata and is excluded. Sequence index and all
        traffic/configuration-derived domain values remain in the comparison.
        """
        data = self.to_dict()
        data.pop("timestamp_seconds")
        return data

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(
            self.to_dict(), ensure_ascii=False, allow_nan=False, indent=indent, sort_keys=True
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TrafficSnapshot":
        expected = {
            "schema_version", "timestamp_seconds", "sequence_index", "source",
            "intersections", "diagnostics",
        }
        _require_keys(data, expected, cls.__name__)
        return cls(
            schema_version=data["schema_version"],
            timestamp_seconds=data["timestamp_seconds"],
            sequence_index=data["sequence_index"],
            source=TrafficSource(data["source"]),
            intersections=tuple(IntersectionSnapshot.from_dict(item) for item in data["intersections"]),
            diagnostics=TrafficDiagnostics.from_dict(data["diagnostics"]),
        )

    @classmethod
    def from_json(cls, payload: str) -> "TrafficSnapshot":
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("TrafficSnapshot JSON root must be an object")
        return cls.from_dict(data)
