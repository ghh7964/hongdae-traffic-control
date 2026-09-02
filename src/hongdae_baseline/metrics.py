from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping
import xml.etree.ElementTree as ET

import numpy as np


RESULT_COLUMNS = (
    "mode",
    "controller",
    "seed",
    "master_seed",
    "python_seed",
    "numpy_seed",
    "torch_seed",
    "random_trips_seed",
    "duarouter_seed",
    "route_seed",
    "env_seed",
    "route_hash",
    "route_file",
    "sumo_seed",
    "checkpoint",
    "checkpoint_sha256",
    "vecnormalize",
    "vecnormalize_sha256",
    "generated_vehicle_count",
    "departed_vehicle_count",
    "arrived_vehicle_count",
    "unfinished_vehicle_count",
    "completion_rate",
    "final_network_vehicle_count",
    "tripinfo_vehicle_count",
    "avg_vehicle_waiting_time",
    "p95_vehicle_waiting_time",
    "max_vehicle_waiting_time",
    "avg_time_loss",
    "throughput",
    "max_queue",
    "teleport_count",
)


@dataclass(frozen=True)
class VehicleMetrics:
    generated_vehicle_count: int
    departed_vehicle_count: int
    arrived_vehicle_count: int
    unfinished_vehicle_count: int
    completion_rate: float
    final_network_vehicle_count: int
    avg_vehicle_waiting_time: float
    p95_vehicle_waiting_time: float
    max_vehicle_waiting_time: float
    avg_time_loss: float
    throughput: int
    max_queue: int
    teleport_count: int
    tripinfo_vehicle_count: int

    @classmethod
    def from_tripinfo(
        cls,
        tripinfo_path: Path,
        throughput: int,
        generated_vehicle_count: int,
        departed_vehicle_count: int,
        arrived_vehicle_count: int,
        final_network_vehicle_count: int,
        max_queue: int,
        teleport_count: int,
    ) -> "VehicleMetrics":
        if not tripinfo_path.is_file():
            raise FileNotFoundError(f"SUMO did not produce tripinfo output: {tripinfo_path}")
        trips = ET.parse(tripinfo_path).getroot().findall("tripinfo")
        if not trips:
            raise ValueError(f"No vehicle tripinfo records in {tripinfo_path}")
        generated_vehicle_count = int(generated_vehicle_count)
        departed_vehicle_count = int(departed_vehicle_count)
        arrived_vehicle_count = int(arrived_vehicle_count)
        final_network_vehicle_count = int(final_network_vehicle_count)
        if int(throughput) != arrived_vehicle_count:
            raise ValueError(
                f"throughput ({throughput}) must equal arrived_vehicle_count ({arrived_vehicle_count})"
            )
        if not 0 <= arrived_vehicle_count <= departed_vehicle_count <= generated_vehicle_count:
            raise ValueError(
                "Vehicle counts must satisfy 0 <= arrived <= departed <= generated; "
                f"got {arrived_vehicle_count}, {departed_vehicle_count}, {generated_vehicle_count}"
            )
        unfinished_vehicle_count = generated_vehicle_count - arrived_vehicle_count
        if not 0 <= final_network_vehicle_count <= unfinished_vehicle_count:
            raise ValueError(
                "final_network_vehicle_count must be between zero and unfinished_vehicle_count; "
                f"got {final_network_vehicle_count} and {unfinished_vehicle_count}"
            )
        waits = np.asarray([float(trip.get("waitingTime", "0")) for trip in trips], dtype=np.float64)
        losses = np.asarray([float(trip.get("timeLoss", "0")) for trip in trips], dtype=np.float64)
        return cls(
            generated_vehicle_count=generated_vehicle_count,
            departed_vehicle_count=departed_vehicle_count,
            arrived_vehicle_count=arrived_vehicle_count,
            unfinished_vehicle_count=unfinished_vehicle_count,
            completion_rate=(arrived_vehicle_count / generated_vehicle_count if generated_vehicle_count else 0.0),
            final_network_vehicle_count=final_network_vehicle_count,
            avg_vehicle_waiting_time=float(np.mean(waits)),
            p95_vehicle_waiting_time=float(np.percentile(waits, 95)),
            max_vehicle_waiting_time=float(np.max(waits)),
            avg_time_loss=float(np.mean(losses)),
            throughput=int(throughput),
            max_queue=int(max_queue),
            teleport_count=int(teleport_count),
            tripinfo_vehicle_count=len(trips),
        )

    def to_dict(self) -> dict[str, int | float]:
        return {
            "generated_vehicle_count": self.generated_vehicle_count,
            "departed_vehicle_count": self.departed_vehicle_count,
            "arrived_vehicle_count": self.arrived_vehicle_count,
            "unfinished_vehicle_count": self.unfinished_vehicle_count,
            "completion_rate": self.completion_rate,
            "final_network_vehicle_count": self.final_network_vehicle_count,
            "avg_vehicle_waiting_time": self.avg_vehicle_waiting_time,
            "p95_vehicle_waiting_time": self.p95_vehicle_waiting_time,
            "max_vehicle_waiting_time": self.max_vehicle_waiting_time,
            "avg_time_loss": self.avg_time_loss,
            "throughput": self.throughput,
            "max_queue": self.max_queue,
            "teleport_count": self.teleport_count,
            "tripinfo_vehicle_count": self.tripinfo_vehicle_count,
        }


def write_results_csv(path: Path, records: Iterable[Mapping[str, object]]) -> None:
    rows = list(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
