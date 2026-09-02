from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

import numpy as np


MIN_GAP = 2.5


class TraciLike(Protocol):
    lane: object
    vehicle: object


def custom_hongdae_observation(
    sumo: TraciLike,
    lanes: Sequence[str],
    lane_lengths: dict[str, float],
    green_phase: int,
    num_green_phases: int = 3,
) -> np.ndarray:
    """Faithful extraction of the notebook's 21-dimensional V5 observation."""
    phase = [1.0 if green_phase == index else 0.0 for index in range(num_green_phases)]
    density: list[float] = []
    queue: list[float] = []
    waits: list[float] = []
    for lane_id in lanes:
        mean_vehicle_length = float(sumo.lane.getLastStepLength(lane_id))
        capacity = lane_lengths[lane_id] / (MIN_GAP + mean_vehicle_length)
        capacity = max(capacity, np.finfo(np.float32).eps)
        density.append(min(1.0, float(sumo.lane.getLastStepVehicleNumber(lane_id)) / capacity))
        queue.append(min(1.0, float(sumo.lane.getLastStepHaltingNumber(lane_id)) / capacity))
        vehicle_ids = sumo.lane.getLastStepVehicleIDs(lane_id)
        max_wait = max((float(sumo.vehicle.getWaitingTime(vehicle)) for vehicle in vehicle_ids), default=0.0)
        waits.append(min(max_wait / 100.0, 1.0))
    observation = np.asarray(phase + density + queue + waits, dtype=np.float32)
    expected = num_green_phases + 3 * len(lanes)
    if observation.shape != (expected,):
        raise AssertionError(f"Observation shape {observation.shape} != {(expected,)}")
    if not np.all(np.isfinite(observation)) or np.any(observation < 0.0) or np.any(observation > 1.0):
        raise ValueError("V5 observation escaped its declared [0, 1] range")
    return observation


@dataclass
class LegacyV5Reward:
    """Stateful faithful extraction of custom_safe_reward from the legacy notebook."""

    last_penalty: float | None = None

    def __call__(self, sumo: TraciLike, lanes: Sequence[str]) -> float:
        queue = sum(float(sumo.lane.getLastStepHaltingNumber(lane)) for lane in lanes)
        starvation = 0.0
        for lane in lanes:
            for vehicle in sumo.lane.getLastStepVehicleIDs(lane):
                wait = float(sumo.vehicle.getWaitingTime(vehicle))
                if wait > 60:
                    starvation += 3
                if wait > 120:
                    starvation += 7
        penalty = queue + starvation
        if self.last_penalty is None:
            self.last_penalty = penalty
            return 0.0
        reward = self.last_penalty - penalty
        self.last_penalty = penalty
        return float(np.clip(reward, -5.0, 5.0))

