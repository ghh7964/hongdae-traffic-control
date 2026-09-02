from __future__ import annotations

from pathlib import Path
import unittest

import numpy as np

from hongdae_baseline.assets import AssetManifest
from hongdae_baseline.config import load_config
from hongdae_baseline.policy import DeterministicPPOPolicy


ROOT = Path(__file__).resolve().parents[1]


class ModelManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config("corrected_baseline", ROOT)
        cls.assets = AssetManifest(cls.config.asset_manifest, ROOT)

    def test_all_immutable_assets_match_manifest(self) -> None:
        verified = self.assets.verify_all()
        self.assertIn("ppo_v5_170k", verified)
        self.assertIn("ppo_v5_200k", verified)

    def test_model_and_vecnormalize_dimensions_match(self) -> None:
        for controller in ("PPO_V5_170K", "PPO_V5_200K"):
            with self.subTest(controller=controller):
                policy = DeterministicPPOPolicy.load(self.assets, controller)
                self.assertEqual(policy.observation_dim, 21)
                self.assertEqual(policy.normalizer.observation_dim, 21)
                self.assertEqual(policy.action_count, 3)
                self.assertIn(policy.predict(np.zeros(21, dtype=np.float32)), range(3))

    def test_checkpoints_use_their_own_normalization_statistics(self) -> None:
        selected = DeterministicPPOPolicy.load(self.assets, "PPO_V5_170K")
        end = DeterministicPPOPolicy.load(self.assets, "PPO_V5_200K")
        self.assertEqual(selected.training_steps, 170000)
        self.assertEqual(end.training_steps, 200000)
        self.assertAlmostEqual(selected.normalizer.count, 170001.0001)
        self.assertAlmostEqual(end.normalizer.count, 200001.0001)
        self.assertNotEqual(selected.normalizer_asset.sha256, end.normalizer_asset.sha256)
        self.assertEqual(self.assets.asset("ppo_v5_170k").role, "selected_legacy")
        self.assertEqual(self.assets.asset("ppo_v5_200k").role, "training_end")


if __name__ == "__main__":
    unittest.main()

