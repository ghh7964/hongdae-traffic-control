from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class Asset:
    name: str
    path: Path
    role: str
    sha256: str
    metadata: dict[str, Any]

    def verify(self) -> None:
        if not self.path.is_file():
            raise FileNotFoundError(f"Missing asset {self.name}: {self.path}")
        actual = sha256_file(self.path)
        if actual != self.sha256:
            raise ValueError(f"Checksum mismatch for {self.name}: expected {self.sha256}, got {actual}")


class AssetManifest:
    def __init__(self, manifest_path: Path, root: Path):
        self.path = manifest_path.resolve()
        self.root = root.resolve()
        self.raw = json.loads(self.path.read_text(encoding="utf-8"))

    def asset(self, name: str, verify: bool = True) -> Asset:
        entry = self.raw["assets"][name]
        asset = Asset(
            name=name,
            path=(self.root / entry["path"]).resolve(),
            role=entry["role"],
            sha256=entry["sha256"],
            metadata={key: value for key, value in entry.items() if key not in {"path", "role", "sha256"}},
        )
        if verify:
            asset.verify()
        return asset

    def verify_all(self) -> dict[str, str]:
        verified: dict[str, str] = {}
        for name in self.raw["assets"]:
            asset = self.asset(name, verify=True)
            verified[name] = asset.sha256
        return verified


def find_sumo_home() -> Path | None:
    candidates: list[Path] = []
    configured = os.environ.get("SUMO_HOME")
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        (
            Path("/Library/Frameworks/EclipseSUMO.framework/Versions/Current/EclipseSUMO/share/sumo"),
            Path("/opt/homebrew/opt/sumo/share/sumo"),
            Path("/usr/local/share/sumo"),
        )
    )
    for candidate in candidates:
        if (candidate / "tools" / "randomTrips.py").is_file():
            return candidate.resolve()
    return None


def find_sumo_binary(name: str = "sumo") -> Path | None:
    found = shutil.which(name)
    if found:
        return Path(found).resolve()
    candidates = [
        Path("/Library/Frameworks/EclipseSUMO.framework/Versions/Current/EclipseSUMO/bin") / name,
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
    ]
    home = find_sumo_home()
    if home:
        candidates.extend((home / "bin" / name, home.parent.parent / "bin" / name))
    return next((path.resolve() for path in candidates if path.is_file() and os.access(path, os.X_OK)), None)


def configure_sumo_runtime() -> Path | None:
    """Prefer a valid official pkg install over a stale inherited SUMO_HOME."""
    home = find_sumo_home()
    if home is None:
        return None
    os.environ["SUMO_HOME"] = str(home)
    binary_dir = home.parent.parent / "bin"
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    if str(binary_dir) not in path_entries:
        os.environ["PATH"] = os.pathsep.join((str(binary_dir), *path_entries))
    framework_root = home.parent.parent / "framework" / "EclipseSUMO.framework" / "Versions"
    proj_databases = sorted(framework_root.glob("*/EclipseSUMO/share/proj/proj.db"), reverse=True)
    if proj_databases:
        os.environ["PROJ_DATA"] = str(proj_databases[0].parent)
    tools = str(home / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    return home


def _command_version(binary: Path | None) -> str | None:
    if binary is None:
        return None
    completed = subprocess.run([str(binary), "--version"], check=False, capture_output=True, text=True)
    first = (completed.stdout or completed.stderr).splitlines()
    return first[0].strip() if first else f"exit={completed.returncode}"


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def runtime_environment() -> dict[str, Any]:
    discovered_home = configure_sumo_runtime()
    binaries = {name: find_sumo_binary(name) for name in ("sumo", "netconvert", "duarouter")}
    sumo_home = str(discovered_home) if discovered_home else os.environ.get("SUMO_HOME")
    random_trips = Path(sumo_home) / "tools" / "randomTrips.py" if sumo_home else None
    python_modules: dict[str, dict[str, str | None]] = {}
    for name in ("traci", "sumolib"):
        try:
            module = importlib.import_module(name)
            python_modules[name] = {
                "file": str(Path(module.__file__).resolve()) if getattr(module, "__file__", None) else None,
                "version": getattr(module, "__version__", None),
            }
        except Exception as exc:
            python_modules[name] = {"file": None, "version": f"import failed: {exc}"}
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "sumo_home": sumo_home,
        "sumo_tools_present": bool(random_trips and random_trips.is_file()),
        "binaries": {
            name: {"path": str(path) if path else None, "version": _command_version(path)}
            for name, path in binaries.items()
        },
        "packages": {
            name: _package_version(name)
            for name in (
                "traci",
                "sumolib",
                "sumo-rl",
                "stable-baselines3",
                "gymnasium",
                "numpy",
                "torch",
                "cloudpickle",
            )
        },
        "python_modules": python_modules,
    }
