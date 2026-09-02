#!/usr/bin/env python3
"""Export deterministic SB3 inference fixtures from the trusted legacy environment.

Run this script in the Colab environment that can load Stable-Baselines3 2.8.0.
The output contains only small observations and inference values, never model weights.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import pickle
import platform
from typing import Any

import numpy as np


CONTROLLERS = {
    "PPO_V5_170K": "ppo_v5_170k",
    "PPO_V5_200K": "ppo_v5_200k",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def default_observations() -> list[list[float]]:
    cases: list[np.ndarray] = []
    profiles = (
        (np.zeros(6), np.zeros(6), np.zeros(6)),
        (np.linspace(0.0, 1.0, 6), np.linspace(1.0, 0.0, 6), np.full(6, 0.25)),
        (np.full(6, 1.0), np.full(6, 0.75), np.linspace(0.0, 1.0, 6)),
        (np.asarray([0.05, 0.9, 0.15, 0.7, 0.25, 0.5]), np.asarray([0.0, 0.8, 0.1, 0.6, 0.2, 0.4]), np.asarray([0.2, 1.0, 0.3, 0.8, 0.4, 0.6])),
    )
    for index, (density, queue, waiting) in enumerate(profiles):
        phase = np.zeros(3, dtype=np.float32)
        phase[index % 3] = 1.0
        cases.append(np.concatenate((phase, density, queue, waiting)).astype(np.float32))
    return [case.tolist() for case in cases]


def load_observations(path: Path | None) -> list[list[float]]:
    if path is None:
        observations = default_observations()
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
        observations = value.get("observations", value) if isinstance(value, dict) else value
    array = np.asarray(observations, dtype=np.float32)
    if array.ndim != 2 or array.shape[1] != 21:
        raise ValueError(f"Expected an N x 21 observation matrix, got {array.shape}")
    if not np.all(np.isfinite(array)) or np.any(array < 0) or np.any(array > 1):
        raise ValueError("Raw observations must be finite and within [0, 1]")
    return array.tolist()


def export_controller(
    repo_root: Path,
    controller: str,
    observations: list[list[float]],
    output_dir: Path,
) -> Path:
    import stable_baselines3
    import torch
    from stable_baselines3 import PPO

    manifest_path = repo_root / "configs" / "assets_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assets = manifest["assets"]
    model_meta = assets[CONTROLLERS[controller]]
    vec_meta = assets[model_meta["vecnormalize"]]
    model_path = repo_root / model_meta["path"]
    vec_path = repo_root / vec_meta["path"]
    if sha256(model_path) != model_meta["sha256"] or sha256(vec_path) != vec_meta["sha256"]:
        raise ValueError(f"Checksum verification failed for {controller}")

    model = PPO.load(model_path, device="cpu")
    with vec_path.open("rb") as handle:
        vecnormalize = pickle.load(handle)
    vecnormalize.training = False
    vecnormalize.norm_reward = False

    cases: list[dict[str, Any]] = []
    for case_index, raw_values in enumerate(observations):
        raw = np.asarray(raw_values, dtype=np.float32)
        normalized = np.asarray(vecnormalize.normalize_obs(raw.copy()), dtype=np.float32)
        action, _ = model.predict(normalized, deterministic=True)
        obs_tensor, _ = model.policy.obs_to_tensor(normalized)
        with torch.no_grad():
            features = model.policy.extract_features(obs_tensor)
            policy_features = features[0] if isinstance(features, tuple) else features
            latent_policy = model.policy.mlp_extractor.forward_actor(policy_features)
            logits = model.policy.action_net(latent_policy).detach().cpu().numpy().reshape(-1)
        cases.append(
            {
                "case_id": case_index,
                "raw_observation": raw.tolist(),
                "normalized_observation": normalized.tolist(),
                "deterministic_action": int(np.asarray(action).reshape(-1)[0]),
                "logits": logits.astype(np.float32).tolist(),
            }
        )

    fixture = {
        "schema_version": 1,
        "controller": controller,
        "checkpoint_sha256": model_meta["sha256"],
        "vecnormalize_sha256": vec_meta["sha256"],
        "source": "Stable-Baselines3 PPO.predict(deterministic=True) in legacy-compatible environment",
        "versions": {
            "python": platform.python_version(),
            "stable_baselines3": stable_baselines3.__version__,
            "numpy": np.__version__,
            "torch": torch.__version__,
        },
        "cases": cases,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = "170k" if controller.endswith("170K") else "200k"
    output_path = output_dir / f"ppo_parity_{suffix}.json"
    output_path.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--controller", choices=("all", *CONTROLLERS), default="all")
    parser.add_argument("--observations", type=Path, help="Optional JSON N x 21 raw observation matrix")
    parser.add_argument("--output-dir", type=Path, default=Path("tests/fixtures"))
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    observations = load_observations(args.observations)
    controllers = tuple(CONTROLLERS) if args.controller == "all" else (args.controller,)
    for controller in controllers:
        path = export_controller(repo_root, controller, observations, (repo_root / args.output_dir).resolve())
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
