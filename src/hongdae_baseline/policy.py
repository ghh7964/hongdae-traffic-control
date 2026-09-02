from __future__ import annotations

from dataclasses import dataclass
import io
import json
from pathlib import Path
import pickle
import zipfile

import numpy as np

from .assets import Asset, AssetManifest


CONTROLLER_ASSETS = {
    "PPO_V5_170K": "ppo_v5_170k",
    "PPO_V5_200K": "ppo_v5_200k",
}


@dataclass(frozen=True)
class VecNormalizeStats:
    mean: np.ndarray
    variance: np.ndarray
    count: float
    epsilon: float
    clip_obs: float
    observation_dim: int
    action_count: int

    @classmethod
    def load(cls, path: Path) -> "VecNormalizeStats":
        # These trusted pickle files are checksum-verified immutable project assets.
        with path.open("rb") as handle:
            normalizer = pickle.load(handle)
        mean = np.asarray(normalizer.obs_rms.mean, dtype=np.float64)
        variance = np.asarray(normalizer.obs_rms.var, dtype=np.float64)
        return cls(
            mean=mean,
            variance=variance,
            count=float(normalizer.obs_rms.count),
            epsilon=float(normalizer.epsilon),
            clip_obs=float(normalizer.clip_obs),
            observation_dim=int(normalizer.observation_space.shape[0]),
            action_count=int(normalizer.action_space.n),
        )

    def normalize(self, observation: np.ndarray) -> np.ndarray:
        observation = np.asarray(observation, dtype=np.float32)
        if observation.shape != (self.observation_dim,):
            raise ValueError(f"Observation shape {observation.shape} != {(self.observation_dim,)}")
        normalized = (observation - self.mean) / np.sqrt(self.variance + self.epsilon)
        return np.clip(normalized, -self.clip_obs, self.clip_obs).astype(np.float32)


@dataclass(frozen=True)
class DeterministicPPOPolicy:
    checkpoint: Path
    checkpoint_sha256: str
    normalizer_asset: Asset
    normalizer: VecNormalizeStats
    layers: tuple[tuple[np.ndarray, np.ndarray], ...]
    action_layer: tuple[np.ndarray, np.ndarray]
    observation_dim: int
    action_count: int
    training_steps: int

    @classmethod
    def load(cls, manifest: AssetManifest, controller: str) -> "DeterministicPPOPolicy":
        if controller not in CONTROLLER_ASSETS:
            raise ValueError(f"No PPO checkpoint for controller {controller}")
        model_asset = manifest.asset(CONTROLLER_ASSETS[controller])
        normalizer_name = model_asset.metadata["vecnormalize"]
        normalizer_asset = manifest.asset(normalizer_name)
        normalizer = VecNormalizeStats.load(normalizer_asset.path)
        with zipfile.ZipFile(model_asset.path) as archive:
            metadata = json.loads(archive.read("data"))
            import torch

            state = torch.load(io.BytesIO(archive.read("policy.pth")), map_location="cpu", weights_only=True)
        layer_indexes = sorted(
            int(key.split(".")[2])
            for key in state
            if key.startswith("mlp_extractor.policy_net.") and key.endswith(".weight")
        )
        layers = tuple(
            (
                state[f"mlp_extractor.policy_net.{index}.weight"].detach().cpu().numpy(),
                state[f"mlp_extractor.policy_net.{index}.bias"].detach().cpu().numpy(),
            )
            for index in layer_indexes
        )
        action_layer = (
            state["action_net.weight"].detach().cpu().numpy(),
            state["action_net.bias"].detach().cpu().numpy(),
        )
        observation_dim = int(layers[0][0].shape[1])
        action_count = int(action_layer[0].shape[0])
        if observation_dim != normalizer.observation_dim or action_count != normalizer.action_count:
            raise ValueError(
                f"Model/VecNormalize mismatch: model ({observation_dim}, {action_count}), "
                f"normalizer ({normalizer.observation_dim}, {normalizer.action_count})"
            )
        return cls(
            checkpoint=model_asset.path,
            checkpoint_sha256=model_asset.sha256,
            normalizer_asset=normalizer_asset,
            normalizer=normalizer,
            layers=layers,
            action_layer=action_layer,
            observation_dim=observation_dim,
            action_count=action_count,
            training_steps=int(metadata["num_timesteps"]),
        )

    def predict_logits(self, observation: np.ndarray) -> np.ndarray:
        hidden = self.normalizer.normalize(observation)
        for weight, bias in self.layers:
            hidden = np.tanh(weight @ hidden + bias)
        weight, bias = self.action_layer
        return (weight @ hidden + bias).astype(np.float32)

    def predict(self, observation: np.ndarray) -> int:
        return int(np.argmax(self.predict_logits(observation)))

    def manifest_fields(self) -> dict[str, object]:
        return {
            "checkpoint": str(self.checkpoint),
            "checkpoint_sha256": self.checkpoint_sha256,
            "checkpoint_steps": self.training_steps,
            "vecnormalize": str(self.normalizer_asset.path),
            "vecnormalize_sha256": self.normalizer_asset.sha256,
            "vecnormalize_count": self.normalizer.count,
        }
