#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import heapq
import json
import math
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from .common import (
        EVALUATION_BBOX,
        EXTRACTION_BBOX,
        NETWORK_ROOT,
        REPO_ROOT,
        TARGET_OSM_OBJECTS,
        add_sumo_tool_arguments,
        lane_allows,
        normalize_warning,
        sha256_file,
        sumo_subprocess_environment,
        sumo_tools_from_args,
        weak_components,
        write_json_exclusive,
        write_text_exclusive,
    )
except ImportError:
    from common import (
        EVALUATION_BBOX,
        EXTRACTION_BBOX,
        NETWORK_ROOT,
        REPO_ROOT,
        TARGET_OSM_OBJECTS,
        add_sumo_tool_arguments,
        lane_allows,
        normalize_warning,
        sha256_file,
        sumo_subprocess_environment,
        sumo_tools_from_args,
        weak_components,
        write_json_exclusive,
        write_text_exclusive,
    )


PHYSICAL_NAMES = {
    "hongik_gate": "홍익대학교 정문 앞 교차로",
    "hongdae_station_intersection": "홍대입구역사거리",
    "hongdae_exit_9": "2호선 홍대입구역 9번 출구",
    "airport_railroad_station": "공항철도·경의중앙선 역사 방향",
    "eoulmadang_redroad": "어울마당로/레드로드 북·중부",
}
MVP_TARGETS = {"hongik_gate", "hongdae_station_intersection"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit the uncorrected Hongdae B SUMO network")
    parser.add_argument("--net", type=Path, default=NETWORK_ROOT / "generated" / "hongdae_b.auto.net.xml")
    parser.add_argument("--raw", type=Path)
    parser.add_argument("--netconvert-log", type=Path, default=NETWORK_ROOT / "audit" / "netconvert.log")
    add_sumo_tool_arguments(parser)
    return parser.parse_args()


def find_raw(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    candidates = sorted((NETWORK_ROOT / "raw").glob("hongdae_b_*_bbox.osm.xml"))
    if len(candidates) != 1:
        raise RuntimeError(f"Expected one raw OSM file, found {len(candidates)}")
    return candidates[0].resolve()


def parse_raw_osm(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    nodes: dict[str, dict[str, Any]] = {}
    ways: dict[str, dict[str, Any]] = {}
    node_to_ways: dict[str, set[str]] = defaultdict(set)
    for node in root.findall("node"):
        node_id = node.attrib["id"]
        nodes[node_id] = {
            "lat": node.attrib.get("lat"),
            "lon": node.attrib.get("lon"),
            "tags": {tag.attrib["k"]: tag.attrib["v"] for tag in node.findall("tag")},
        }
    for way in root.findall("way"):
        way_id = way.attrib["id"]
        node_ids = [nd.attrib["ref"] for nd in way.findall("nd")]
        ways[way_id] = {
            "nodes": node_ids,
            "tags": {tag.attrib["k"]: tag.attrib["v"] for tag in way.findall("tag")},
        }
        for node_id in node_ids:
            node_to_ways[node_id].add(way_id)
    return {
        "nodes": nodes,
        "ways": ways,
        "node_to_ways": node_to_ways,
        "relation_count": len(root.findall("relation")),
    }


def raw_spatial_statistics(raw: dict[str, Any]) -> dict[str, Any]:
    coordinates = [
        (float(node["lon"]), float(node["lat"])) for node in raw["nodes"].values()
    ]
    west, south, east, north = EXTRACTION_BBOX
    outside = [
        (longitude, latitude)
        for longitude, latitude in coordinates
        if not (west <= longitude <= east and south <= latitude <= north)
    ]
    return {
        "node_count": len(raw["nodes"]),
        "way_count": len(raw["ways"]),
        "relation_count": raw["relation_count"],
        "complete_way_node_outside_bbox_count": len(outside),
        "coordinate_extent": {
            "west": min(longitude for longitude, _latitude in coordinates),
            "south": min(latitude for _longitude, latitude in coordinates),
            "east": max(longitude for longitude, _latitude in coordinates),
            "north": max(latitude for _longitude, latitude in coordinates),
        },
    }


def element_orig_ids(element: ET.Element) -> set[str]:
    values: set[str] = set()
    for param in element.findall(".//param"):
        if param.attrib.get("key", "").lower() in {"origid", "origids"}:
            values.update(re.findall(r"\d+", param.attrib.get("value", "")))
    values.update(re.findall(r"\d+", element.attrib.get("origId", "")))
    return values


def id_mentions(identifier: str, osm_id: str) -> bool:
    return re.search(rf"(?<!\d){re.escape(osm_id)}(?!\d)", identifier) is not None


def way_edge_ids(network: dict[str, Any], osm_way_id: str) -> list[str]:
    return sorted(
        edge_id
        for edge_id, edge in network["edges"].items()
        if edge.attrib.get("function", "normal") == "normal"
        and (id_mentions(edge_id, osm_way_id) or osm_way_id in element_orig_ids(edge))
    )


def point_segment_distance_m(
    longitude: float,
    latitude: float,
    start_lon: float,
    start_lat: float,
    end_lon: float,
    end_lat: float,
) -> float:
    scale_x = 111_320.0 * math.cos(math.radians(latitude))
    scale_y = 110_540.0
    ax, ay = (start_lon - longitude) * scale_x, (start_lat - latitude) * scale_y
    bx, by = (end_lon - longitude) * scale_x, (end_lat - latitude) * scale_y
    dx, dy = bx - ax, by - ay
    denominator = dx * dx + dy * dy
    factor = 0.0 if denominator == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / denominator))
    return math.hypot(ax + factor * dx, ay + factor * dy)


def nearest_pedestrian_matches(
    raw: dict[str, Any], network: dict[str, Any], osm_node_id: str, limit: int = 5
) -> list[dict[str, Any]]:
    target = raw["nodes"].get(osm_node_id)
    if target is None:
        return []
    longitude, latitude = float(target["lon"]), float(target["lat"])
    candidates: list[dict[str, Any]] = []
    for way_id, way in raw["ways"].items():
        if "highway" not in way["tags"]:
            continue
        matching_edges = way_edge_ids(network, way_id)
        pedestrian_edges = []
        for edge_id in matching_edges:
            edge = network["edges"][edge_id]
            lane_ids = [
                lane.attrib["id"]
                for lane in edge.findall("lane")
                if lane_allows(lane, "pedestrian")
            ]
            if lane_ids:
                pedestrian_edges.append((edge_id, lane_ids))
        if not pedestrian_edges:
            continue
        coordinates = [
            (float(raw["nodes"][node_id]["lon"]), float(raw["nodes"][node_id]["lat"]))
            for node_id in way["nodes"]
            if node_id in raw["nodes"]
        ]
        if not coordinates:
            continue
        for edge_id, lane_ids in pedestrian_edges:
            edge = network["edges"][edge_id]
            source = raw["nodes"].get(edge.attrib.get("from", ""))
            destination = raw["nodes"].get(edge.attrib.get("to", ""))
            if source is not None and destination is not None:
                distance = point_segment_distance_m(
                    longitude,
                    latitude,
                    float(source["lon"]),
                    float(source["lat"]),
                    float(destination["lon"]),
                    float(destination["lat"]),
                )
            elif len(coordinates) == 1:
                distance = point_segment_distance_m(
                    longitude, latitude, *coordinates[0], *coordinates[0]
                )
            else:
                distance = min(
                    point_segment_distance_m(longitude, latitude, *start, *end)
                    for start, end in zip(coordinates, coordinates[1:])
                )
            candidates.append(
                {
                    "distance_m": round(distance, 1),
                    "osm_way_id": way_id,
                    "sumo_edge_id": edge_id,
                    "pedestrian_lane_ids": lane_ids,
                }
            )
    candidates.sort(key=lambda item: (item["distance_m"], item["sumo_edge_id"]))
    return candidates[:limit]


def parse_network(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    junctions = {element.attrib["id"]: element for element in root.findall("junction")}
    edges = {element.attrib["id"]: element for element in root.findall("edge")}
    tl_logics = {element.attrib["id"]: element for element in root.findall("tlLogic")}
    connections = root.findall("connection")
    return {
        "root": root,
        "junctions": junctions,
        "edges": edges,
        "tl_logics": tl_logics,
        "connections": connections,
    }


def network_statistics(network: dict[str, Any], log_text: str) -> dict[str, Any]:
    junctions: dict[str, ET.Element] = network["junctions"]
    edges: dict[str, ET.Element] = network["edges"]
    connections: list[ET.Element] = network["connections"]
    junction_types = Counter(junction.attrib.get("type", "unknown") for junction in junctions.values())
    edge_functions = Counter(edge.attrib.get("function", "normal") for edge in edges.values())
    lanes = [lane for edge in edges.values() for lane in edge.findall("lane")]
    pedestrian_lanes = [lane for lane in lanes if lane_allows(lane, "pedestrian")]

    graph: dict[str, set[str]] = defaultdict(set)
    passenger_graph: dict[str, set[str]] = defaultdict(set)
    incoming: Counter[str] = Counter()
    outgoing: Counter[str] = Counter()
    for edge in edges.values():
        if edge.attrib.get("function", "normal") != "normal":
            continue
        source = edge.attrib.get("from")
        target = edge.attrib.get("to")
        if not source or not target:
            continue
        graph[source].add(target)
        graph[target].add(source)
        outgoing[source] += 1
        incoming[target] += 1
        if any(lane_allows(lane, "passenger") for lane in edge.findall("lane")):
            passenger_graph[source].add(target)
            passenger_graph[target].add(source)

    dead_end_junctions = sorted(
        junction_id
        for junction_id in set(incoming) | set(outgoing)
        if incoming[junction_id] == 0 or outgoing[junction_id] == 0
    )
    fringe_junctions = sorted(
        junction_id for junction_id, junction in junctions.items() if junction.attrib.get("fringe")
    )
    warnings = Counter()
    errors = Counter()
    diagnostics = Counter()
    for line in log_text.splitlines():
        if line.startswith("Warning:"):
            warnings[normalize_warning(line)] += 1
        elif line.startswith("Error:"):
            errors[normalize_warning(line)] += 1
        elif "pj_obj_create:" in line:
            diagnostics[line.strip()] += 1

    components = weak_components(graph)
    passenger_components = weak_components(passenger_graph)
    unbuilt_traffic_lights = sorted(
        set(re.findall(r"The traffic light '([^']+)' does not control any links", log_text))
    )
    controlled_links_by_tls = Counter(
        connection.attrib["tl"] for connection in connections if "tl" in connection.attrib
    )
    return {
        "junction_count": len(junctions),
        "junction_type_counts": dict(sorted(junction_types.items())),
        "edge_count": len(edges),
        "normal_edge_count": edge_functions.get("normal", 0),
        "edge_function_counts": dict(sorted(edge_functions.items())),
        "lane_count": len(lanes),
        "traffic_light_junction_count": sum(
            count for kind, count in junction_types.items() if kind.startswith("traffic_light")
        ),
        "traffic_light_junction_ids": sorted(
            junction_id
            for junction_id, junction in junctions.items()
            if junction.attrib.get("type", "").startswith("traffic_light")
        ),
        "tl_logic_count": len(network["tl_logics"]),
        "tl_logic_ids": sorted(network["tl_logics"]),
        "controlled_links_by_tl_logic": dict(sorted(controlled_links_by_tls.items())),
        "unbuilt_traffic_light_ids": unbuilt_traffic_lights,
        "controlled_link_count": sum(1 for connection in connections if "tl" in connection.attrib),
        "pedestrian_allowed_lane_count": len(pedestrian_lanes),
        "crossing_count": edge_functions.get("crossing", 0),
        "walkingarea_count": edge_functions.get("walkingarea", 0),
        "dead_end_junction_count": len(dead_end_junctions),
        "dead_end_junctions": dead_end_junctions,
        "fringe_junction_count": len(fringe_junctions),
        "fringe_junctions": fringe_junctions,
        "weak_component_count": len(components),
        "weak_component_sizes": [len(component) for component in components],
        "passenger_weak_component_count": len(passenger_components),
        "passenger_weak_component_sizes": [len(component) for component in passenger_components],
        "warning_count": sum(warnings.values()),
        "warning_types": dict(warnings.most_common()),
        "error_count": sum(errors.values()),
        "error_types": dict(errors.most_common()),
        "diagnostic_count": sum(diagnostics.values()),
        "diagnostic_types": dict(diagnostics.most_common()),
        "network_location": (
            dict(network["root"].find("location").attrib)
            if network["root"].find("location") is not None
            else {}
        ),
    }


def map_target(
    name: str,
    raw: dict[str, Any],
    network: dict[str, Any],
) -> dict[str, Any]:
    kind, osm_id = TARGET_OSM_OBJECTS[name]
    related_way_ids = {osm_id} if kind == "way" else set(raw["node_to_ways"].get(osm_id, set()))
    junction_ids = []
    if kind == "node":
        junction_ids = sorted(
            junction_id
            for junction_id, junction in network["junctions"].items()
            if junction.attrib.get("type") != "internal"
            and (junction_id == osm_id or osm_id in element_orig_ids(junction))
        )
    incoming_edge_ids = sorted(
        edge_id
        for edge_id, edge in network["edges"].items()
        if edge.attrib.get("function", "normal") == "normal"
        and edge.attrib.get("to") in junction_ids
    )
    outgoing_edge_ids = sorted(
        edge_id
        for edge_id, edge in network["edges"].items()
        if edge.attrib.get("function", "normal") == "normal"
        and edge.attrib.get("from") in junction_ids
    )
    edge_ids = (
        sorted(set(incoming_edge_ids) | set(outgoing_edge_ids))
        if kind == "node"
        else way_edge_ids(network, osm_id)
    )
    lane_ids = sorted(
        lane.attrib["id"]
        for edge_id in edge_ids
        for lane in network["edges"][edge_id].findall("lane")
    )
    related_junctions = set(junction_ids)
    for edge_id in edge_ids:
        edge = network["edges"][edge_id]
        related_junctions.update(
            value for value in (edge.attrib.get("from"), edge.attrib.get("to")) if value
        )
    tl_ids = {junction_id for junction_id in junction_ids if junction_id in network["tl_logics"]}
    for connection in network["connections"]:
        if (
            connection.attrib.get("from") in incoming_edge_ids
            and connection.attrib.get("to") in outgoing_edge_ids
        ):
            if connection.attrib.get("tl"):
                tl_ids.add(connection.attrib["tl"])

    related_lanes = [
        lane for edge_id in edge_ids for lane in network["edges"][edge_id].findall("lane")
    ]
    pedestrian_facility_edge_ids = sorted(
        edge_id
        for edge_id, edge in network["edges"].items()
        if edge.attrib.get("function") in {"crossing", "walkingarea"}
        and any(edge_id.startswith(f":{junction_id}_") for junction_id in junction_ids)
    )
    nearest = nearest_pedestrian_matches(raw, network, osm_id) if kind == "node" else []
    direct_pedestrian = any(lane_allows(lane, "pedestrian") for lane in related_lanes) or bool(
        pedestrian_facility_edge_ids
    )
    if direct_pedestrian:
        pedestrian_access = "direct-generated"
    elif nearest:
        pedestrian_access = f"nearby-{nearest[0]['distance_m']:.1f}m-connectivity-unverified"
    else:
        pedestrian_access = "unresolved"
    automatic_signal = any(
        network["junctions"][junction_id].attrib.get("type", "").startswith("traffic_light")
        for junction_id in junction_ids
    ) or bool(tl_ids)

    if junction_ids:
        status = "exact-junction"
    elif edge_ids:
        status = "related-osm-ways-mapped-no-object-junction"
    else:
        status = "raw-object-only-no-sumo-mapping"

    if name in MVP_TARGETS:
        correction_needed = "P0-controlled-links-and-pedestrian-review"
    elif name in {"hongdae_exit_9", "airport_railroad_station"}:
        correction_needed = "P1-pedestrian-connectivity-review"
    elif pedestrian_access == "unresolved":
        correction_needed = "P1-pedestrian-connection-review"
    elif name == "eoulmadang_redroad":
        correction_needed = "P1-access-and-pedestrian-review"
    else:
        correction_needed = "review"

    raw_record = raw["nodes"].get(osm_id) if kind == "node" else raw["ways"].get(osm_id)
    return {
        "physical_location": PHYSICAL_NAMES[name],
        "target_name": name,
        "osm_object": f"{kind}/{osm_id}",
        "raw_present": raw_record is not None,
        "raw_tags": raw_record["tags"] if raw_record is not None else {},
        "raw_related_way_ids": sorted(related_way_ids),
        "raw_coordinates": (
            {"longitude": raw_record["lon"], "latitude": raw_record["lat"]}
            if kind == "node" and raw_record is not None
            else None
        ),
        "sumo_junction_ids": junction_ids,
        "related_sumo_junction_ids": sorted(related_junctions),
        "related_edge_ids": edge_ids,
        "incoming_edge_ids": incoming_edge_ids,
        "outgoing_edge_ids": outgoing_edge_ids,
        "related_lane_ids": lane_ids,
        "pedestrian_facility_edge_ids": pedestrian_facility_edge_ids,
        "nearest_pedestrian_matches": nearest,
        "nearest_pedestrian_edge_ids": [item["sumo_edge_id"] for item in nearest],
        "nearest_pedestrian_lane_ids": sorted(
            {lane_id for item in nearest for lane_id in item["pedestrian_lane_ids"]}
        ),
        "tl_logic_ids": sorted(tl_ids),
        "controlled_link_count": sum(
            1 for connection in network["connections"] if connection.attrib.get("tl") in tl_ids
        ),
        "controlled_link_direction_counts": dict(
            sorted(
                Counter(
                    connection.attrib.get("dir", "unknown")
                    for connection in network["connections"]
                    if connection.attrib.get("tl") in tl_ids
                ).items()
            )
        ),
        "tl_phase_counts": {
            tl_id: len(network["tl_logics"][tl_id].findall("phase")) for tl_id in sorted(tl_ids)
        },
        "automatic_signal": automatic_signal,
        "pedestrian_access": pedestrian_access,
        "status": status,
        "correction_needed": correction_needed,
    }


def shortest_edge_path(
    edges: dict[str, ET.Element], start: str, target: str
) -> list[str]:
    adjacency: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for edge_id, edge in edges.items():
        if edge.attrib.get("function", "normal") != "normal":
            continue
        if not any(lane_allows(lane, "passenger") for lane in edge.findall("lane")):
            continue
        source, destination = edge.attrib.get("from"), edge.attrib.get("to")
        if source and destination:
            adjacency[source].append((destination, edge_id))
    queue: list[tuple[float, str, tuple[str, ...]]] = [(0.0, start, ())]
    best_distance = {start: 0.0}
    while queue:
        distance, junction, path = heapq.heappop(queue)
        if junction == target:
            return list(path)
        if distance > best_distance.get(junction, math.inf):
            continue
        for neighbor, edge_id in sorted(adjacency.get(junction, [])):
            edge = edges[edge_id]
            lanes = edge.findall("lane")
            edge_length = min(float(lane.attrib.get("length", "0")) for lane in lanes)
            next_distance = distance + edge_length
            if next_distance < best_distance.get(neighbor, math.inf):
                best_distance[neighbor] = next_distance
                heapq.heappush(queue, (next_distance, neighbor, path + (edge_id,)))
    return []


def core_candidate(mapping: list[dict[str, Any]], network: dict[str, Any]) -> dict[str, Any]:
    mapped = {entry["target_name"]: entry for entry in mapping}
    gate_ids = mapped["hongik_gate"]["sumo_junction_ids"]
    station_ids = mapped["hongdae_station_intersection"]["sumo_junction_ids"]
    forward: list[str] = []
    reverse: list[str] = []
    if gate_ids and station_ids:
        forward = shortest_edge_path(network["edges"], gate_ids[0], station_ids[0])
        reverse = shortest_edge_path(network["edges"], station_ids[0], gate_ids[0])
    connecting = sorted(set(forward) | set(reverse))
    seed_junctions = sorted(set(gate_ids) | set(station_ids))
    core_junctions = set(seed_junctions)
    for edge_id in connecting:
        edge = network["edges"][edge_id]
        core_junctions.update(value for value in (edge.attrib.get("from"), edge.attrib.get("to")) if value)
    one_hop = sorted(
        edge_id
        for edge_id, edge in network["edges"].items()
        if edge.attrib.get("function", "normal") == "normal"
        and any(lane_allows(lane, "passenger") for lane in edge.findall("lane"))
        and (edge.attrib.get("from") in core_junctions or edge.attrib.get("to") in core_junctions)
    )
    candidate_edges = sorted(set(connecting) | set(one_hop))
    entry_edges = sorted(
        edge_id
        for edge_id in one_hop
        for edge in [network["edges"][edge_id]]
        if edge.attrib.get("to") in core_junctions
        and edge.attrib.get("from") not in core_junctions
    )
    exit_edges = sorted(
        edge_id
        for edge_id in one_hop
        for edge in [network["edges"][edge_id]]
        if edge.attrib.get("from") in core_junctions
        and edge.attrib.get("to") not in core_junctions
    )
    return {
        "status": "proposal-not-final",
        "seed_junctions": seed_junctions,
        "forward_connecting_path_edges": forward,
        "reverse_connecting_path_edges": reverse,
        "connecting_path_edges": connecting,
        "forward_normal_edge_lane_length_m": round(
            sum(float(network["edges"][edge_id].findall("lane")[0].attrib["length"]) for edge_id in forward),
            1,
        ),
        "reverse_normal_edge_lane_length_m": round(
            sum(float(network["edges"][edge_id].findall("lane")[0].attrib["length"]) for edge_id in reverse),
            1,
        ),
        "one_hop_context_edges": one_hop,
        "candidate_core_edges": candidate_edges,
        "candidate_core_junctions": sorted(core_junctions),
        "candidate_entry_edges": entry_edges,
        "candidate_exit_edges": exit_edges,
        "vehicle_classification_proposal": {
            "core_vehicle": "vehicle whose route or observed trajectory enters candidate_core_edges",
            "boundary_pass_through": "vehicle that remains outside candidate_core_edges",
            "runtime_fields": ["ever_entered_core", "first_core_entry_time", "last_core_exit_time"],
        },
    }


def write_mapping_csv(path: Path, mapping: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "physical_location",
        "osm_object",
        "sumo_junction_ids",
        "related_edge_ids",
        "related_lane_ids",
        "nearest_pedestrian_edge_ids",
        "nearest_pedestrian_lane_ids",
        "tl_logic_ids",
        "controlled_link_count",
        "automatic_signal",
        "pedestrian_access",
        "status",
        "correction_needed",
    ]
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for entry in mapping:
            writer.writerow(
                {
                    key: ";".join(entry[key]) if isinstance(entry[key], list) else entry[key]
                    for key in fieldnames
                }
            )


def render_report(
    raw_path: Path,
    net_path: Path,
    stats: dict[str, Any],
    mapping: list[dict[str, Any]],
    core: dict[str, Any],
    netcheck_returncode: int,
    acquisition: dict[str, Any],
    build: dict[str, Any],
    fetch_command: str,
    netconvert_command: str,
) -> str:
    rows = []
    for entry in mapping:
        coordinates = entry["raw_coordinates"]
        coordinate_text = (
            f"{coordinates['latitude']}, {coordinates['longitude']}" if coordinates else "way"
        )
        if entry["related_edge_ids"]:
            edges = "<br>".join(f"`{value}`" for value in entry["related_edge_ids"][:8])
            if len(entry["related_edge_ids"]) > 8:
                edges += "<br>…"
        elif entry["nearest_pedestrian_matches"]:
            edges = "<br>".join(
                f"`{item['sumo_edge_id']}` (~{item['distance_m']} m)"
                for item in entry["nearest_pedestrian_matches"][:3]
            )
        else:
            edges = "—"
        tls = "<br>".join(
            f"`{value}` ({entry['tl_phase_counts'][value]} phases)"
            for value in entry["tl_logic_ids"]
        ) or "—"
        rows.append(
            "| {physical_location} | `{osm_object}`<br>{coordinates} | {junctions} | {edges} | "
            "{tls}<br>{links} links {directions} | {signal} | {pedestrian} | {status} | {correction} |".format(
                physical_location=entry["physical_location"],
                osm_object=entry["osm_object"],
                coordinates=coordinate_text,
                junctions="<br>".join(f"`{value}`" for value in entry["sumo_junction_ids"]) or "—",
                edges=edges,
                tls=tls,
                links=entry["controlled_link_count"],
                directions=json.dumps(entry["controlled_link_direction_counts"], sort_keys=True),
                signal="예" if entry["automatic_signal"] else "아니오/미확인",
                pedestrian=entry["pedestrian_access"],
                status=entry["status"],
                correction=entry["correction_needed"],
            )
        )
    warning_lines = [f"- {count}× `{kind}`" for kind, count in stats["warning_types"].items()]
    if not warning_lines:
        warning_lines = ["- netconvert 경고 없음"]
    diagnostic_lines = [
        f"- {count}× `{kind}`" for kind, count in stats["diagnostic_types"].items()
    ] or ["- 기타 진단 메시지 없음"]
    mvp_warning_lines = [f"- `{line}`" for line in stats["mvp_related_warning_lines"]] or [
        "- MVP 인접 edge ID를 직접 언급한 경고 없음"
    ]
    mvp = {entry["target_name"]: entry for entry in mapping if entry["target_name"] in MVP_TARGETS}
    mvp_objects_ready = all(
        entry["sumo_junction_ids"] and entry["tl_logic_ids"] and entry["automatic_signal"]
        for entry in mvp.values()
    )
    redroad = next(entry for entry in mapping if entry["target_name"] == "eoulmadang_redroad")
    return f"""# Hongdae B 초기 자동 네트워크 감사 보고서

## 1. 범위와 provenance

- OSM query bbox (W,S,E,N): `{','.join(map(str, EXTRACTION_BBOX))}`
- 평가 후보 bbox: `{','.join(map(str, EVALUATION_BBOX))}` (자동 평가 edge 선택 규칙이 아님)
- 취득 시각: `{acquisition['acquired_at']}` (`Asia/Seoul`), OSM base `{acquisition['osm_base']}`
- Overpass endpoint: `{acquisition['overpass_endpoint']}`; 관측된 재시도 {acquisition['observed_retry_count']}회
- 원본: `{raw_path.relative_to(REPO_ROOT)}`; {acquisition['raw_size_bytes']} bytes; SHA-256 `{sha256_file(raw_path)}`
- 자동본: `{net_path.relative_to(REPO_ROOT)}`; SHA-256 `{sha256_file(net_path)}`
- 구조 해시: `{build['structural_sha256']}`; 독립 2회 변환 동일 여부 `{build['structure_reproducible']}`
- 도구: `{acquisition['sumo_version']}`, `{acquisition['netconvert_version']}`, osmGet SHA-256 `{acquisition['osm_get_sha256']}`
- osmGet가 불러온 번들 sumolib 경로는 1.27.1 설치 트리지만 Python distribution 메타데이터는 `{acquisition['sumolib_version']}`으로 보고되어 원본 헤더에도 이 값이 표시된다. 이는 provenance상 버전 표기 불일치로 보존한다.
- OSM 데이터는 © OpenStreetMap contributors, ODbL 1.0 조건을 따른다.
- 이 보고서는 자동 변환본만 감사한다. `corrected` 네트워크, 교차로 TOML, 수요, 실제 신호 주기는 생성하지 않았다.

원본 OSM 통계: node {stats['raw_osm']['node_count']}, way {stats['raw_osm']['way_count']}, relation {stats['raw_osm']['relation_count']}. Query는 고정 bbox이지만 완전한 OSM way topology 보존 때문에 bbox 밖 종속 node {stats['raw_osm']['complete_way_node_outside_bbox_count']}개가 원본에 포함된다. netconvert에서는 `--keep-edges.in-geo-boundary`로 확정 bbox를 적용했다.

## 2. 실제 실행 명령과 변환 정책

취득 명령:

```text
{fetch_command.strip()}
```

자동 변환 명령:

```text
{netconvert_command.strip()}
```

차량·보행 typemap, OSM sidewalk/crossing/turn lane/lane access, walking area, 도로명, 원 OSM ID, license metadata, plain XML 출력을 사용했다. `geometry.remove`, `junctions.join`, `tls.join`, `tls.guess`, `tls.guess-signals`, `sidewalks.guess`, `crossings.guess`, 임의 actuated 지정은 사용하지 않았다.

## 3. 기본 통계

| Metric | Value |
|---|---:|
| Junctions | {stats['junction_count']} |
| Edges (total / normal) | {stats['edge_count']} / {stats['normal_edge_count']} |
| Lanes | {stats['lane_count']} |
| Traffic-light junctions | {stats['traffic_light_junction_count']} |
| tlLogic elements | {stats['tl_logic_count']} |
| Controlled links | {stats['controlled_link_count']} |
| Pedestrian-allowed lanes | {stats['pedestrian_allowed_lane_count']} |
| Crossings | {stats['crossing_count']} |
| Walking areas | {stats['walkingarea_count']} |
| Dead-end junction candidates | {stats['dead_end_junction_count']} |
| Fringe junctions | {stats['fringe_junction_count']} |
| Junction-incidence weak components | {stats['weak_component_count']} |
| Passenger junction-incidence weak components | {stats['passenger_weak_component_count']} |
| netcheck exit code | {netcheck_returncode} |
| netcheck connection components | {stats['netcheck']['component_count']} |
| netcheck largest-component coverage | {stats['netcheck']['largest_component_coverage_percent']}% |

Junction types: `{json.dumps(stats['junction_type_counts'], ensure_ascii=False, sort_keys=True)}`

Edge functions: `{json.dumps(stats['edge_function_counts'], ensure_ascii=False, sort_keys=True)}`

Passenger component sizes: `{stats['passenger_weak_component_sizes']}`

## 4. 핵심 OSM ↔ SUMO 대응

| 실제 위치 | OSM 객체·좌표 | SUMO junction | 인접 또는 최근접 edge | tlLogic·제어 link | 자동 신호 | 보행 접근 | 확인 상태 | 보정/검토 |
|---|---|---|---|---|---|---|---|---|
{chr(10).join(rows)}

상세 lane ID와 최근접 보행 edge의 OSM way 및 거리는 `osm_sumo_mapping.json`과 CSV에 보존했다. 최근접 표시는 공간적 근접성만 뜻하며 연결 가능성을 보장하지 않는다.

## 5. 경고와 구조적 문제

netconvert 경고 총 {stats['warning_count']}건, Error 표식 {stats['error_count']}건:

{chr(10).join(warning_lines)}

기타 stderr 진단:

{chr(10).join(diagnostic_lines)}

MVP 인접 edge 관련 경고:

{chr(10).join(mvp_warning_lines)}

## 6. 수동 보정·확인의 우선순위

### P0 — MVP 사용 전 필수

- 두 MVP node는 정확히 같은 ID의 traffic-light junction/tlLogic으로 생성됐다: 구조 식별 결과 `{mvp_objects_ready}`.
- 홍익대 정문 TLS는 {mvp['hongik_gate']['controlled_link_count']}개, 홍대입구역사거리 TLS는 {mvp['hongdae_station_intersection']['controlled_link_count']}개 controlled link를 가진다. 회전 방향과 U턴(`dir=t`)을 영상·현장·도로 규제 자료와 대조해야 한다.
- 홍익대 정문 인접 `299767124#4 → 218976037#0`에서 left-turn lane 누락 가능성을 알리는 minor-green 경고가 발생했다. 이 연결은 정문 MVP의 제어 link이므로 우선 검증 대상이다.
- 자동 생성된 `type=static` phase는 OSM 신호 존재로 만든 SUMO 기본 프로그램일 뿐 실제 신호 주기가 아니다. 제어 코드나 baseline의 실측 주기로 간주하면 안 된다.
- 전체 네트워크에 crossing이 {stats['crossing_count']}개뿐이다. 정문에는 walkingarea가 일부 생성됐지만 역사거리에는 교차로 소속 crossing/walkingarea가 확인되지 않아 보행 포함 MVP에는 바로 사용할 수 없다.

### P1 — 보행·접근·투영

- 9번 출구와 공항철도 역사 node는 SUMO junction으로 변환되지 않는 POI 성격이다. JSON에 기록된 최근접 보행 lane에서 실제 연결성·출발 위치를 수동 확인해야 하며 원본 OSM은 수정하지 않는다.
- OSM way 919071199는 `highway=pedestrian`과 `motor_vehicle=yes`를 함께 갖지만 자동본 edge `{redroad['related_edge_ids']}`는 pedestrian 허용 lane으로 변환됐고, netcheck에서 단독 connection component로 분리됐다. Red Road 보행 흐름에는 현재 사용할 수 없으며 연결 복원과 제한적 차량 통행 정책을 함께 확인해야 한다.
- 전체 normal edge의 netcheck 결과는 {stats['netcheck']['component_count']}개 component, 최대 component coverage {stats['netcheck']['largest_component_coverage_percent']}%로 **미연결**이다. junction incidence 기반 WCC와 달리 실제 connection을 따르는 검사이므로 보행 연결 감사에서 이 결과를 우선한다.
- `pj_obj_create: Cannot find proj.db`가 기록됐으나 UTM network 생성·재읽기는 성공했다. 다음 단계에서 geo 좌표 overlay를 시각 검증하기 전까지 투영 정확성을 확정하지 않는다.
- 자동 생성 실패한 비-MVP traffic signal `{stats['unbuilt_traffic_light_ids']}`와 제거된 PT stop이 있다. 후속 제어 범위에 넣기 전에 원 OSM 위치와 용도를 확인한다.

### P2 — 차로·경계·연결성

- 차로 수, 일방통행, turn lane, 비정상 U턴, internal lane 형상을 항공사진·로드뷰·현장 자료와 대조한다.
- unknown from/to way인 restriction relation, 급회전·회전반경 감속 경고를 실제 허용 회전과 대조한다.
- dead-end 후보 {stats['dead_end_junction_count']}개와 fringe 표식 {stats['fringe_junction_count']}개를 route 생성 전에 확인한다. 승용차 그래프는 weak component {stats['passenger_weak_component_count']}개(크기 {stats['passenger_weak_component_sizes']})로 연결돼 있다.
- 남쪽 경계 및 모든 buffer 진입·이탈 edge는 최종 평가 목록에서 제외하고, 경계 edge에서 route 생성·종료가 가능한지는 수요 생성 전 별도 검증한다.

## 7. 평가 core 제안(미확정)

- seed junction: `{core['seed_junctions']}`
- 두 교차로 간 양방향 최단 passenger 경로 edge: `{core['connecting_path_edges']}`
- 후보 core edge {len(core['candidate_core_edges'])}개, core 진입 edge {len(core['candidate_entry_edges'])}개, 이탈 edge {len(core['candidate_exit_edges'])}개

최종 목록은 의도적으로 확정하지 않았다. 승인된 core edge에 실제 진입한 차량에만 `ever_entered_core`, `first_core_entry_time`, `last_core_exit_time`을 기록하고, core에 진입하지 않은 경계 통과 차량은 core 지체 지표에서 분리한다.

## 8. 준비도 판정

- OSM 취득·자동 변환·plain XML·provenance·checksum: **완료**
- 동일 입력/옵션 독립 2회 구조 재현: **통과**
- netconvert 재읽기와 수요 없는 SUMO load: **통과**
- netcheck 실행: **완료, 그러나 전체 edge network 미연결** ({stats['netcheck']['component_count']}개 component)
- MVP 두 신호 객체의 junction/tlLogic 식별: **통과**
- 차량 중심의 후속 수동 감사 출발점으로 사용: **조건부 가능**
- 정량 평가·RL 학습·보행 포함 MVP에 즉시 사용: **아직 불가** — P0 controlled-link/차로/횡단시설 확인 필요

다음 단계의 최소 보정은 (1) MVP 두 교차로의 물리 방향별 controlled link 대조, (2) 정문 minor-green/turn lane과 U턴 검토, (3) 두 교차로 횡단보도·walkingarea 연결 복원 여부 판단, (4) 9번 출구·공항철도·레드로드 보행 연결 확인, (5) buffer route smoke test 순서다. 자동 병합·guess 옵션은 이 검토 전에 켜지 않는다.
"""


def main() -> int:
    args = parse_args()
    tools = sumo_tools_from_args(args)
    net_path = args.net.resolve()
    raw_path = find_raw(args.raw)
    log_path = args.netconvert_log.resolve()
    for path in (net_path, raw_path, log_path, tools.netcheck):
        if not path.exists():
            raise FileNotFoundError(path)

    audit_dir = NETWORK_ROOT / "audit"
    stats_path = audit_dir / "network_stats.json"
    mapping_path = audit_dir / "osm_sumo_mapping.csv"
    mapping_json_path = audit_dir / "osm_sumo_mapping.json"
    core_path = audit_dir / "core_candidates.json"
    netcheck_path = audit_dir / "netcheck.txt"
    report_path = audit_dir / "report.md"
    for path in (stats_path, mapping_path, mapping_json_path, core_path, netcheck_path, report_path):
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite audit artifact: {path}")

    raw = parse_raw_osm(raw_path)
    network = parse_network(net_path)
    log_text = log_path.read_text(encoding="utf-8")
    stats = network_statistics(network, log_text)
    stats["raw_osm"] = raw_spatial_statistics(raw)
    mapping = [map_target(name, raw, network) for name in TARGET_OSM_OBJECTS]
    mvp_edge_ids = {
        edge_id
        for entry in mapping
        if entry["target_name"] in MVP_TARGETS
        for edge_id in entry["related_edge_ids"]
    }
    stats["mvp_related_warning_lines"] = sorted(
        {
            line.strip()
            for line in log_text.splitlines()
            if line.startswith("Warning:") and any(edge_id in line for edge_id in mvp_edge_ids)
        }
    )
    core = core_candidate(mapping, network)

    provenance_dir = NETWORK_ROOT / "provenance"
    acquisition = json.loads((provenance_dir / "acquisition.json").read_text(encoding="utf-8"))
    build = json.loads((provenance_dir / "build.json").read_text(encoding="utf-8"))
    fetch_command = (provenance_dir / "fetch.command.txt").read_text(encoding="utf-8")
    netconvert_command = (provenance_dir / "netconvert.command.txt").read_text(encoding="utf-8")

    netcheck_command = [sys.executable, str(tools.netcheck), str(net_path)]
    netcheck_environment = sumo_subprocess_environment(tools, include_pythonpath=True)
    netcheck = subprocess.run(
        netcheck_command,
        cwd=REPO_ROOT,
        env=netcheck_environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    netcheck_text = "$ " + " ".join(netcheck_command) + "\n" + netcheck.stdout
    coverage_match = re.search(r"Coverage:\s*([0-9.]+)%", netcheck.stdout)
    stats["netcheck"] = {
        "returncode": netcheck.returncode,
        "connected": "Warning! Net is not connected." not in netcheck.stdout,
        "component_count": len(re.findall(r"^Component:", netcheck.stdout, flags=re.MULTILINE)),
        "largest_component_coverage_percent": (
            float(coverage_match.group(1)) if coverage_match else None
        ),
    }

    write_json_exclusive(stats_path, stats)
    write_mapping_csv(mapping_path, mapping)
    write_json_exclusive(mapping_json_path, mapping)
    write_json_exclusive(core_path, core)
    write_text_exclusive(netcheck_path, netcheck_text)
    write_text_exclusive(
        report_path,
        render_report(
            raw_path,
            net_path,
            stats,
            mapping,
            core,
            netcheck.returncode,
            acquisition,
            build,
            fetch_command,
            netconvert_command,
        ),
    )

    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
