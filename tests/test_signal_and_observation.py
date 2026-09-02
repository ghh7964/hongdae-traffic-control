from __future__ import annotations

from pathlib import Path
import unittest

import numpy as np

from hongdae_baseline.config import load_config
from hongdae_baseline.legacy_v5 import LegacyV5Reward, custom_hongdae_observation
from hongdae_baseline.signal import PPOPhaseController, TLSDefinition


ROOT = Path(__file__).resolve().parents[1]


class _LaneAPI:
    def __init__(self, lanes: tuple[str, ...]):
        self.lanes = lanes

    def getLastStepLength(self, lane: str) -> float:
        return 5.0

    def getLastStepVehicleNumber(self, lane: str) -> int:
        return self.lanes.index(lane) * 2

    def getLastStepHaltingNumber(self, lane: str) -> int:
        return self.lanes.index(lane)

    def getLastStepVehicleIDs(self, lane: str) -> tuple[str, ...]:
        index = self.lanes.index(lane)
        return () if index == 0 else (f"veh{index}",)


class _VehicleAPI:
    def getWaitingTime(self, vehicle: str) -> float:
        return float(vehicle.removeprefix("veh")) * 30.0


class _Sumo:
    def __init__(self, lanes: tuple[str, ...]):
        self.lane = _LaneAPI(lanes)
        self.vehicle = _VehicleAPI()


class SignalAndObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config("corrected_baseline", ROOT)
        cls.definition = TLSDefinition.from_network(cls.config.network, cls.config.tls_id)

    def test_existing_observation_is_21d_and_bounded(self) -> None:
        lanes = self.definition.incoming_lanes
        observation = custom_hongdae_observation(
            _Sumo(lanes), lanes, {lane: 50.0 for lane in lanes}, green_phase=1
        )
        self.assertEqual(observation.shape, (21,))
        self.assertEqual(observation.dtype, np.float32)
        self.assertTrue(np.all(observation >= 0.0))
        self.assertTrue(np.all(observation <= 1.0))
        self.assertEqual(observation[:3].tolist(), [0.0, 1.0, 0.0])

    def test_faithful_reward_clips_and_resets_per_instance(self) -> None:
        lanes = self.definition.incoming_lanes
        reward = LegacyV5Reward()
        self.assertEqual(reward(_Sumo(lanes), lanes), 0.0)
        self.assertGreaterEqual(reward(_Sumo(lanes), lanes), -5.0)
        self.assertEqual(LegacyV5Reward()(_Sumo(lanes), lanes), 0.0)

    def test_corrected_yellow_matches_network_six_seconds(self) -> None:
        self.assertEqual(self.config.yellow_time, 6)
        self.assertEqual(set(self.definition.native_yellow_durations), {6.0})

    def test_corrected_max_green_forces_transition(self) -> None:
        states: list[str] = []
        controller = PPOPhaseController(
            self.definition.green_states,
            min_green=15,
            yellow_time=6,
            max_green=50,
            enforce_max_green=True,
        )
        controller.initialize(states.append)
        for _ in range(50):
            controller.tick(states.append)
        self.assertTrue(controller.is_yellow)
        self.assertEqual(controller.forced_transitions, 1)
        self.assertIn("y", states[-1])
        for _ in range(6):
            controller.tick(states.append)
        self.assertFalse(controller.is_yellow)
        self.assertEqual(controller.green_phase, 1)

    def test_legacy_mode_documents_ignored_max_green(self) -> None:
        config = load_config("legacy_compatible", ROOT)
        states: list[str] = []
        controller = PPOPhaseController(
            self.definition.green_states,
            min_green=config.min_green,
            yellow_time=config.yellow_time,
            max_green=config.max_green,
            enforce_max_green=config.enforce_max_green,
        )
        controller.initialize(states.append)
        for _ in range(100):
            controller.tick(states.append)
        self.assertEqual(controller.forced_transitions, 0)
        self.assertEqual(controller.green_phase, 0)


if __name__ == "__main__":
    unittest.main()

