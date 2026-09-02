from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import tomllib


CONTROLLERS = ("FIXED_TIME", "ACTUATED", "PPO_V5_170K", "PPO_V5_200K")
MODES = ("legacy_compatible", "corrected_baseline")


@dataclass(frozen=True)
class EvaluationConfig:
    root: Path
    mode: str
    network: Path
    route_template: Path
    asset_manifest: Path
    results_dir: Path
    tls_id: str
    horizon_seconds: int
    demand_end_seconds: int
    vehicle_period_seconds: float
    delta_time: int
    min_green: int
    yellow_time: int
    max_green: int
    enforce_max_green: bool
    time_to_teleport: int
    sumo_seed_mode: str
    route_generator: str
    include_pedestrians: bool
    fixed_time_source: str

    def to_manifest(self) -> dict[str, object]:
        data = asdict(self)
        for key in ("root", "network", "route_template", "asset_manifest", "results_dir"):
            data[key] = str(data[key])
        return data


def repository_root(start: Path | None = None) -> Path:
    candidate = (start or Path(__file__)).resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for directory in (candidate, *candidate.parents):
        if (directory / "configs" / "assets_manifest.json").is_file():
            return directory
    raise FileNotFoundError("Could not locate repository root containing configs/assets_manifest.json")


def load_config(mode: str, root: Path | None = None) -> EvaluationConfig:
    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode!r}; choose one of {MODES}")
    root = (root or repository_root()).resolve()
    config_path = root / "configs" / f"{mode}.toml"
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    paths = raw["paths"]
    simulation = raw["simulation"]
    demand = raw["demand"]
    fixed = raw["fixed_time"]
    config = EvaluationConfig(
        root=root,
        mode=raw["mode"],
        network=(root / paths["network"]).resolve(),
        route_template=(root / paths["route_template"]).resolve(),
        asset_manifest=(root / paths["asset_manifest"]).resolve(),
        results_dir=(root / paths["results_dir"]).resolve(),
        tls_id=str(simulation["tls_id"]),
        horizon_seconds=int(simulation["horizon_seconds"]),
        demand_end_seconds=int(simulation["demand_end_seconds"]),
        vehicle_period_seconds=float(simulation["vehicle_period_seconds"]),
        delta_time=int(simulation["delta_time"]),
        min_green=int(simulation["min_green"]),
        yellow_time=int(simulation["yellow_time"]),
        max_green=int(simulation["max_green"]),
        enforce_max_green=bool(simulation["enforce_max_green"]),
        time_to_teleport=int(simulation["time_to_teleport"]),
        sumo_seed_mode=str(simulation["sumo_seed_mode"]),
        route_generator=str(demand["generator"]),
        include_pedestrians=bool(demand["include_pedestrians"]),
        fixed_time_source=str(fixed["source"]),
    )
    if config.mode != mode:
        raise ValueError(f"Config mode mismatch: requested {mode}, file says {config.mode}")
    if config.yellow_time <= 0 or config.max_green <= config.min_green:
        raise ValueError("Signal timing configuration is invalid")
    return config

