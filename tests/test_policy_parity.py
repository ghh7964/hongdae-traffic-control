from __future__ import annotations

import json
from pathlib import Path
import unittest

import numpy as np

from hongdae_baseline.assets import AssetManifest
from hongdae_baseline.policy import DeterministicPPOPolicy


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


class PolicyParityTests(unittest.TestCase):
    def test_manual_policy_matches_exported_sb3_fixtures(self) -> None:
        fixture_paths = sorted(FIXTURES.glob("ppo_parity_*.json"))
        if not fixture_paths:
            self.skipTest(
                "SB3 parity fixtures have not been exported from the Colab legacy environment; "
                "run scripts/export_sb3_parity_fixture.py there first"
            )
        assets = AssetManifest(ROOT / "configs" / "assets_manifest.json", ROOT)
        for path in fixture_paths:
            fixture = json.loads(path.read_text(encoding="utf-8"))
            controller = fixture["controller"]
            policy = DeterministicPPOPolicy.load(assets, controller)
            with self.subTest(controller=controller, fixture=path.name):
                self.assertEqual(policy.checkpoint_sha256, fixture["checkpoint_sha256"])
                self.assertEqual(policy.normalizer_asset.sha256, fixture["vecnormalize_sha256"])
                for case in fixture["cases"]:
                    raw = np.asarray(case["raw_observation"], dtype=np.float32)
                    np.testing.assert_allclose(
                        policy.normalizer.normalize(raw),
                        np.asarray(case["normalized_observation"], dtype=np.float32),
                        rtol=1e-6,
                        atol=1e-6,
                    )
                    if "logits" in case:
                        np.testing.assert_allclose(
                            policy.predict_logits(raw),
                            np.asarray(case["logits"], dtype=np.float32),
                            rtol=1e-5,
                            atol=1e-5,
                        )
                    self.assertEqual(policy.predict(raw), int(case["deterministic_action"]))


if __name__ == "__main__":
    unittest.main()
