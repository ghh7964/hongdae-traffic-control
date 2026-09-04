from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
NETWORK_ROOT = REPO_ROOT / "networks" / "hongdae_b"

EXTRACTION_BBOX = (126.9168, 37.5510, 126.9296, 37.5605)
EVALUATION_BBOX = (126.9188, 37.5510, 126.9283, 37.5590)
OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"

MACOS_SUMO_PREFIX = Path(
    "/Library/Frameworks/EclipseSUMO.framework/Versions/1.27.1/EclipseSUMO"
)


@dataclass(frozen=True)
class SumoTools:
    sumo_home: Path
    sumo: Path
    netconvert: Path
    osm_get: Path
    netcheck: Path
    vehicle_typemap: Path
    pedestrian_typemap: Path
    proj_data: Path | None
    proj_data_source: str


@dataclass(frozen=True)
class ProjDataResolution:
    path: Path | None
    source: str
    attempted: tuple[Path, ...]

    @property
    def available(self) -> bool:
        return self.path is not None


def _proj_directory(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    return path.parent if path.name == "proj.db" else path


def _valid_proj_directory(value: str | Path) -> Path | None:
    directory = _proj_directory(value)
    return directory if directory.is_dir() and (directory / "proj.db").is_file() else None


def _sumo_proj_candidates(
    *, sumo_home: str | Path | None, sumo_binary: str | Path | None
) -> list[Path]:
    candidates: list[Path] = []
    prefix: Path | None = None
    if sumo_home is not None:
        home = Path(sumo_home).expanduser().resolve()
        candidates.extend([home / "share" / "proj", home / "proj", home.parent / "proj"])
        if home.parent.name == "share":
            prefix = home.parent.parent
    if sumo_binary is not None:
        binary_prefix = Path(sumo_binary).expanduser().resolve().parent.parent
        prefix = prefix or binary_prefix
        candidates.extend(
            [
                binary_prefix / "share" / "proj",
                binary_prefix / "proj",
                binary_prefix / "Resources" / "proj",
            ]
        )

    # Packaged SUMO distributions may embed PROJ below a framework or app
    # bundle rather than next to share/sumo.  Search only bounded bundle roots;
    # never recursively walk a broad prefix such as /usr.
    if prefix is not None:
        for bundle_name in ("framework", "Frameworks", "Contents"):
            bundle_root = prefix / bundle_name
            if bundle_root.is_dir():
                candidates.extend(path.parent for path in bundle_root.glob("**/proj.db"))
    return candidates


def resolve_proj_data(
    explicit: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    sumo_home: str | Path | None = None,
    sumo_binary: str | Path | None = None,
    system_candidates: Sequence[str | Path] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> ProjDataResolution:
    """Resolve a directory containing proj.db using portable precedence.

    An invalid explicit argument is a caller error and raises immediately.
    Invalid environment candidates are recorded and skipped so PROJ_LIB, the
    SUMO installation, or a standard system location may still satisfy the
    request.  Complete failure is represented explicitly as ``unavailable``.
    """
    environment = os.environ if environ is None else environ
    attempted: list[Path] = []

    def inspect(value: str | Path, source: str) -> ProjDataResolution | None:
        directory = _proj_directory(value)
        if directory not in attempted:
            attempted.append(directory)
        valid = _valid_proj_directory(directory)
        if valid is None:
            return None
        return ProjDataResolution(valid, source, tuple(attempted))

    if explicit is not None:
        match = inspect(explicit, "explicit")
        if match is None:
            directory = _proj_directory(explicit)
            raise FileNotFoundError(
                f"Explicit PROJ data directory does not contain proj.db: {directory}"
            )
        return match

    for variable in ("PROJ_DATA", "PROJ_LIB"):
        value = environment.get(variable)
        if value:
            match = inspect(value, f"environment:{variable}")
            if match is not None:
                return match

    for candidate in _sumo_proj_candidates(
        sumo_home=sumo_home, sumo_binary=sumo_binary
    ):
        match = inspect(candidate, "sumo-install")
        if match is not None:
            return match

    candidates = list(system_candidates) if system_candidates is not None else [
        Path(sys.prefix) / "share" / "proj",
        Path("/usr/share/proj"),
        Path("/usr/local/share/proj"),
        Path("/opt/homebrew/share/proj"),
    ]
    for executable in ("projinfo", "proj"):
        hit = which(executable)
        if hit:
            candidates.append(Path(hit).expanduser().resolve().parent.parent / "share" / "proj")
    for candidate in candidates:
        match = inspect(candidate, "system")
        if match is not None:
            return match
    return ProjDataResolution(None, "unavailable", tuple(attempted))


def sumo_subprocess_environment(
    tools: SumoTools,
    *,
    environ: Mapping[str, str] | None = None,
    include_pythonpath: bool = False,
    require_proj: bool = False,
) -> dict[str, str]:
    """Build one consistent environment for SUMO tools and helper scripts."""
    environment = dict(os.environ if environ is None else environ)
    environment["SUMO_HOME"] = str(tools.sumo_home)
    if tools.proj_data is not None:
        environment["PROJ_DATA"] = str(tools.proj_data)
    else:
        environment.pop("PROJ_DATA", None)
        environment.pop("PROJ_LIB", None)
        if require_proj:
            raise RuntimeError(
                "PROJ data unavailable; resolver did not find any directory containing proj.db"
            )
    if include_pythonpath:
        tools_path = str(tools.sumo_home / "tools")
        existing_pythonpath = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            tools_path + os.pathsep + existing_pythonpath if existing_pythonpath else tools_path
        )
    return environment


def _home_candidates(home: Path, name: str) -> list[Path]:
    home = home.expanduser().resolve()
    prefix = home.parent.parent if home.parent.name == "share" else home
    candidates = {
        "sumo": [home / "bin" / "sumo", prefix / "bin" / "sumo"],
        "netconvert": [home / "bin" / "netconvert", prefix / "bin" / "netconvert"],
        "osm_get": [home / "tools" / "osmGet.py"],
        "netcheck": [home / "tools" / "net" / "netcheck.py"],
        "vehicle_typemap": [home / "data" / "typemap" / "osmNetconvert.typ.xml"],
        "pedestrian_typemap": [
            home / "data" / "typemap" / "osmNetconvertPedestrians.typ.xml"
        ],
    }
    return candidates[name]


def _homes_from_binary(binary: Path) -> list[Path]:
    prefix = binary.expanduser().resolve().parent.parent
    return [prefix / "share" / "sumo", prefix]


def resolve_sumo_tools(
    *,
    sumo_home: str | Path | None = None,
    proj_data: str | Path | None = None,
    sumo: str | Path | None = None,
    netconvert: str | Path | None = None,
    osm_get: str | Path | None = None,
    netcheck: str | Path | None = None,
    vehicle_typemap: str | Path | None = None,
    pedestrian_typemap: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    macos_prefix: str | Path = MACOS_SUMO_PREFIX,
    system_proj_candidates: Sequence[str | Path] | None = None,
) -> SumoTools:
    """Resolve one complete SUMO toolchain without assuming an operating system.

    Per-tool precedence is explicit path, explicit/environment SUMO_HOME, PATH
    (including a share/sumo tree inferred from PATH binaries), then the known
    macOS framework installation as a final fallback.
    """
    environment = os.environ if environ is None else environ
    explicit = {
        "sumo": sumo,
        "netconvert": netconvert,
        "osm_get": osm_get,
        "netcheck": netcheck,
        "vehicle_typemap": vehicle_typemap,
        "pedestrian_typemap": pedestrian_typemap,
    }
    if sumo_home is not None:
        selected_home = Path(sumo_home).expanduser().resolve()
        if not selected_home.is_dir():
            raise FileNotFoundError(f"Explicit SUMO_HOME directory does not exist: {selected_home}")
    else:
        environment_home = environment.get("SUMO_HOME")
        environment_candidate = (
            Path(environment_home).expanduser().resolve() if environment_home else None
        )
        selected_home = (
            environment_candidate
            if environment_candidate is not None and environment_candidate.is_dir()
            else None
        )

    path_hits = {
        "sumo": which("sumo"),
        "netconvert": which("netconvert"),
        "osm_get": which("osmGet.py"),
        "netcheck": which("netcheck.py"),
    }
    inferred_homes: list[Path] = []
    for key in ("sumo", "netconvert"):
        if path_hits[key]:
            for home in _homes_from_binary(Path(path_hits[key])):
                if home not in inferred_homes:
                    inferred_homes.append(home)

    fallback_home = Path(macos_prefix).expanduser().resolve() / "share" / "sumo"
    resolved: dict[str, Path] = {}
    attempted: dict[str, list[str]] = {}
    for name, explicit_value in explicit.items():
        if explicit_value is not None:
            explicit_path = Path(explicit_value).expanduser().resolve()
            if not explicit_path.is_file():
                raise FileNotFoundError(
                    f"Explicit SUMO path for {name!r} does not exist: {explicit_path}"
                )
            resolved[name] = explicit_path
            continue

        candidates: list[Path] = []
        if selected_home is not None:
            candidates.extend(_home_candidates(selected_home, name))
        if path_hits.get(name):
            candidates.append(Path(path_hits[name]).expanduser().resolve())
        for inferred_home in inferred_homes:
            candidates.extend(_home_candidates(inferred_home, name))
        candidates.extend(_home_candidates(fallback_home, name))

        unique_candidates: list[Path] = []
        for candidate in candidates:
            if candidate not in unique_candidates:
                unique_candidates.append(candidate)
        attempted[name] = [str(candidate) for candidate in unique_candidates]
        match = next((candidate for candidate in unique_candidates if candidate.is_file()), None)
        if match is not None:
            resolved[name] = match.resolve()

    missing = [name for name in explicit if name not in resolved]
    if missing:
        detail = "\n".join(
            f"- {name}: " + (", ".join(attempted.get(name, [])) or "no candidates")
            for name in missing
        )
        raise FileNotFoundError(
            "Unable to resolve a complete SUMO toolchain. Missing:\n" + detail
        )

    resolved_home = selected_home or resolved["osm_get"].parent.parent
    proj_resolution = resolve_proj_data(
        proj_data,
        environ=environment,
        sumo_home=resolved_home,
        sumo_binary=resolved["sumo"],
        system_candidates=system_proj_candidates,
        which=which,
    )
    return SumoTools(
        sumo_home=resolved_home.resolve(),
        proj_data=proj_resolution.path,
        proj_data_source=proj_resolution.source,
        **resolved,
    )


def add_sumo_tool_arguments(parser: Any) -> None:
    parser.add_argument("--sumo-home", type=Path, help="SUMO share/source root")
    parser.add_argument(
        "--proj-data", type=Path, help="Directory containing proj.db (highest priority)"
    )
    parser.add_argument("--sumo", type=Path, help="Explicit sumo binary")
    parser.add_argument("--netconvert", type=Path, help="Explicit netconvert binary")
    parser.add_argument("--osm-get", type=Path, help="Explicit osmGet.py")
    parser.add_argument("--netcheck", type=Path, help="Explicit netcheck.py")
    parser.add_argument("--vehicle-typemap", type=Path, help="Explicit vehicle typemap")
    parser.add_argument("--pedestrian-typemap", type=Path, help="Explicit pedestrian typemap")


def sumo_tools_from_args(args: Any) -> SumoTools:
    return resolve_sumo_tools(
        sumo_home=args.sumo_home,
        proj_data=args.proj_data,
        sumo=args.sumo,
        netconvert=args.netconvert,
        osm_get=args.osm_get,
        netcheck=args.netcheck,
        vehicle_typemap=args.vehicle_typemap,
        pedestrian_typemap=args.pedestrian_typemap,
    )

TARGET_OSM_OBJECTS = {
    "hongik_gate": ("node", "2959081059"),
    "hongdae_station_intersection": ("node", "3034197250"),
    "hongdae_exit_9": ("node", "3404932011"),
    "airport_railroad_station": ("node", "5919544685"),
    "eoulmadang_redroad": ("way", "919071199"),
}


def bbox_text(bbox: Sequence[float]) -> str:
    return ",".join(f"{value:.4f}" for value in bbox)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def structural_xml_sha256(path: Path) -> str:
    """Hash XML structure while ignoring formatting and comments."""
    digest = hashlib.sha256()
    root = ET.parse(path).getroot()
    for element in root.iter():
        digest.update(element.tag.encode("utf-8"))
        digest.update(b"\0")
        for key, value in sorted(element.attrib.items()):
            digest.update(key.encode("utf-8"))
            digest.update(b"=")
            digest.update(value.encode("utf-8"))
            digest.update(b"\0")
        text = (element.text or "").strip()
        if text:
            digest.update(text.encode("utf-8"))
            digest.update(b"\0")
    return digest.hexdigest()


def run_checked(command: Sequence[str], *, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {completed.returncode}:\n"
            f"{shlex.join(command)}\n{completed.stdout}"
        )
    return completed


def command_text(command: Sequence[str]) -> str:
    return shlex.join(str(part) for part in command)


def write_text_exclusive(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)


def write_json_exclusive(path: Path, value: Any) -> None:
    write_text_exclusive(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def copy_exclusive(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_handle, destination.open("xb") as output_handle:
        shutil.copyfileobj(input_handle, output_handle)


def ensure_paths_absent(paths: Iterable[Path]) -> None:
    present = [str(path) for path in paths if path.exists()]
    if present:
        raise FileExistsError(
            "Refusing to overwrite existing provenance or network files:\n"
            + "\n".join(present)
        )


def git_state(
    expected_head: str | None = None, *, repo_root: Path = REPO_ROOT
) -> dict[str, Any]:
    head = run_checked(["git", "rev-parse", "HEAD"], cwd=repo_root).stdout.strip()
    status = run_checked(
        ["git", "status", "--porcelain=v1"], cwd=repo_root
    ).stdout.splitlines()
    head_matches_expected = (
        None
        if expected_head is None
        else head == expected_head or (len(expected_head) >= 7 and head.startswith(expected_head))
    )
    state = {
        "head": head,
        "expected_head": expected_head,
        "head_matches_expected": head_matches_expected,
        "status_porcelain": status,
        "clean": not status,
    }
    if expected_head is not None and not head_matches_expected:
        raise RuntimeError(f"Expected Git HEAD {expected_head}, found {head}")
    return state


def first_output_line(command: Sequence[str]) -> str:
    return run_checked(command).stdout.splitlines()[0]


def lane_allows(lane: ET.Element, vehicle_class: str) -> bool:
    allowed = set(lane.attrib.get("allow", "").split())
    disallowed = set(lane.attrib.get("disallow", "").split())
    if allowed:
        return vehicle_class in allowed or "all" in allowed
    return vehicle_class not in disallowed and "all" not in disallowed


def normalize_warning(line: str) -> str:
    message = line.strip()
    message = re.sub(r"^(Warning|Error):\s*", "", message)
    message = re.sub(r"'[^']*'", "'<id>'", message)
    message = re.sub(r'"[^"]*"', '"<id>"', message)
    message = re.sub(r"\b-?\d+(?:\.\d+)?\b", "<n>", message)
    return message


def weak_components(adjacency: dict[str, set[str]]) -> list[list[str]]:
    remaining = set(adjacency)
    components: list[list[str]] = []
    while remaining:
        start = min(remaining)
        stack = [start]
        component: set[str] = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(adjacency.get(current, set()) - component)
        remaining -= component
        components.append(sorted(component))
    return sorted(components, key=lambda values: (-len(values), values))


def osm_ids_present(path: Path) -> dict[str, bool]:
    found = {name: False for name in TARGET_OSM_OBJECTS}
    wanted = {(kind, object_id): name for name, (kind, object_id) in TARGET_OSM_OBJECTS.items()}
    for _event, element in ET.iterparse(path, events=("end",)):
        key = (element.tag, element.attrib.get("id", ""))
        name = wanted.get(key)
        if name is not None:
            found[name] = True
        element.clear()
    return found
