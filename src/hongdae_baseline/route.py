from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
from pathlib import Path
import random
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class GeneratedRoute:
    path: Path
    sha256: str
    master_seed: int
    vehicle_count: int
    source: Path
    generator: str = "sample_legacy_routes"

    def to_manifest(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "master_seed": self.master_seed,
            "vehicle_count": self.vehicle_count,
            "source": str(self.source),
            "generator": self.generator,
        }


def route_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def count_generated_vehicles(path: Path) -> int:
    """Return the number of explicitly generated vehicles in a route file."""
    return len(ET.parse(path).getroot().findall("vehicle"))


def _route_candidates(template: Path) -> list[ET.Element]:
    root = ET.parse(template).getroot()
    candidates: list[ET.Element] = []
    route_defs = {route.get("id"): route for route in root.findall("route") if route.get("id")}
    for vehicle in root.findall("vehicle"):
        inline = vehicle.find("route")
        if inline is not None and inline.get("edges"):
            candidates.append(inline)
            continue
        route_id = vehicle.get("route")
        if route_id in route_defs:
            candidates.append(route_defs[route_id])
    if not candidates:
        raise ValueError(f"No vehicle routes found in template {template}")
    return candidates


def generate_route(
    template: Path,
    output: Path,
    master_seed: int,
    end_seconds: int = 400,
    period_seconds: float = 3.5,
) -> GeneratedRoute:
    """Create a deterministic route by sampling valid routes from the immutable legacy route.

    This deliberately avoids randomTrips/duarouter so route generation itself remains usable
    before SUMO system binaries are installed. It generates vehicle demand only; that limitation
    is recorded in the execution manifest and documentation.
    """
    if end_seconds <= 0 or period_seconds <= 0:
        raise ValueError("end_seconds and period_seconds must be positive")
    candidates = _route_candidates(template)
    rng = random.Random(master_seed)
    root = ET.Element("routes")
    root.append(ET.Comment(f" deterministic sampled legacy routes; master_seed={master_seed} "))
    count = 0
    departure = 0.0
    while departure < end_seconds:
        vehicle = ET.SubElement(
            root,
            "vehicle",
            {"id": f"seed{master_seed}_veh{count:04d}", "depart": f"{departure:.2f}"},
        )
        vehicle.append(deepcopy(candidates[rng.randrange(len(candidates))]))
        count += 1
        departure = count * period_seconds
    ET.indent(root, space="  ")
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
    return GeneratedRoute(output.resolve(), route_sha256(output), master_seed, count, template.resolve())


def route_edges(path: Path) -> set[str]:
    root = ET.parse(path).getroot()
    edges: set[str] = set()
    definitions = {route.get("id"): route.get("edges", "") for route in root.findall("route")}
    for route in root.iter("route"):
        edges.update(route.get("edges", "").split())
    for vehicle in root.findall("vehicle"):
        route_id = vehicle.get("route")
        if route_id:
            edges.update(definitions.get(route_id, "").split())
    return edges


def network_edges(path: Path) -> set[str]:
    return {
        edge.get("id", "")
        for edge in ET.parse(path).getroot().findall("edge")
        if edge.get("id") and not edge.get("id", "").startswith(":")
    }


def validate_route_edges(route: Path, network: Path) -> None:
    missing = sorted(route_edges(route) - network_edges(network))
    if missing:
        raise ValueError(f"Route references {len(missing)} missing network edges: {missing[:10]}")
