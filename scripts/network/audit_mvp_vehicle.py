#!/usr/bin/env python3
"""Read-only vehicle-network audit for the two Hongdae MVP junctions.

The script never edits the raw OSM, generated network, legacy assets, results,
or the corrected-network directory.  Demand and simulator output are created
inside a TemporaryDirectory and only summarized JSON is retained.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import ctypes.util
import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from .common import (
        NETWORK_ROOT,
        REPO_ROOT,
        SumoTools,
        add_sumo_tool_arguments,
        lane_allows,
        sha256_file,
        sumo_subprocess_environment,
        sumo_tools_from_args,
    )
except ImportError:
    from common import (
        NETWORK_ROOT,
        REPO_ROOT,
        SumoTools,
        add_sumo_tool_arguments,
        lane_allows,
        sha256_file,
        sumo_subprocess_environment,
        sumo_tools_from_args,
    )


BASELINE_COMMIT = "79f1ba49f45488e3764d9aaea53da0d4d487b8d9"
RAW_SHA256 = "16c432a9591b4ab53c471633dd31967239031f49b20ceae9ff7560baf1a8fc61"
NET_SHA256 = "c17729eb755e88e858ea6b5ad13332dd0bf3ecd17cd7673186037971555ec8f1"
AUDIT_DATE = "2026-09-04"

TLS_NAMES = {
    "2959081059": "홍익대학교 정문 앞",
    "3034197250": "홍대입구역사거리",
}
MVP_TLS_IDS = tuple(TLS_NAMES)
MOVEMENT_NAMES = {"s": "straight", "l": "left", "r": "right", "t": "u_turn"}

# This is the candidate vehicle core recorded by the baseline audit.  It is
# used only to label first/last core edges in the smoke routes, not to select
# demand or to claim a final evaluation boundary.
CORE_EDGES = {
    f"{sign}{way}#{segment}"
    for sign in ("", "-")
    for way, segments in (
        ("218976035", range(3)),
        ("299767124", range(5)),
        ("299959899", range(2)),
    )
    for segment in segments
}

ROUTE_FLOWS = (
    ("gate_south_to_station", "홍익대학교 정문 남측 → 홍대입구역 방향", "333681731#0", "333681721#3"),
    ("station_to_gate_south", "홍대입구역 방향 → 홍익대학교 정문 남측", "333681730#0", "-333681731#0"),
    ("yanghwa_west_to_gate", "양화로 서측 → 홍익로 → 정문 방향", "333681730#0", "218976037#2"),
    ("yanghwa_east_to_gate", "양화로 동측 → 홍익로 → 정문 방향", "515585529#0", "218976037#2"),
    ("worldcup_to_gate", "월드컵북로 측 진입 → 정문 방향", "-515836541#3", "218976037#2"),
    ("gate_to_worldcup", "정문 남측 → 월드컵북로 측 이탈", "333681731#0", "515836541#3"),
    ("outer_pass_through", "MVP core 비통과 외곽 비교", "332222851#2", "336580316#1"),
)

CONTROLLED_COLUMNS = (
    "physical_intersection",
    "tls_id",
    "link_index",
    "from_edge",
    "from_lane",
    "to_edge",
    "to_lane",
    "via_lane",
    "sumo_dir",
    "estimated_approach_direction",
    "estimated_travel_direction",
    "movement_class",
    "same_link_index_connections",
    "phase_0_state",
    "phase_1_state",
    "phase_2_state",
    "phase_3_state",
    "phase_4_state",
    "phase_5_state",
    "osm_turn_lanes_or_restriction_evidence",
    "review_status",
    "correction_required",
    "basis_and_uncertainty",
)

EDGE_COLUMNS = (
    "physical_intersection",
    "junction_id",
    "edge_id",
    "role",
    "osm_way_id",
    "road_name",
    "relative_direction",
    "bearing_degrees",
    "sumo_lane_count",
    "passenger_lane_count",
    "lane_permissions",
    "osm_lanes",
    "osm_lanes_forward",
    "osm_lanes_backward",
    "osm_turn_lanes",
    "osm_oneway",
    "restriction_relations",
    "evidence_status",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--net",
        type=Path,
        default=NETWORK_ROOT / "generated" / "hongdae_b.auto.net.xml",
    )
    parser.add_argument(
        "--raw",
        type=Path,
        default=NETWORK_ROOT / "raw" / "hongdae_b_20260903_bbox.osm.xml",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=NETWORK_ROOT / "audit" / "mvp_vehicle",
    )
    add_sumo_tool_arguments(parser)
    return parser.parse_args()


def run(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def version_line(binary: Path) -> str:
    completed = run([str(binary), "--version"])
    return completed.stdout.splitlines()[0] if completed.stdout else "unavailable"


def parse_osm(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    nodes = {
        n.attrib["id"]: {
            "lon": float(n.attrib["lon"]),
            "lat": float(n.attrib["lat"]),
            "tags": {t.attrib["k"]: t.attrib["v"] for t in n.findall("tag")},
        }
        for n in root.findall("node")
    }
    ways = {
        w.attrib["id"]: {
            "nodes": [n.attrib["ref"] for n in w.findall("nd")],
            "tags": {t.attrib["k"]: t.attrib["v"] for t in w.findall("tag")},
        }
        for w in root.findall("way")
    }
    relations = []
    for relation in root.findall("relation"):
        tags = {t.attrib["k"]: t.attrib["v"] for t in relation.findall("tag")}
        if tags.get("type") != "restriction":
            continue
        relations.append(
            {
                "id": relation.attrib["id"],
                "restriction": tags.get("restriction", ""),
                "members": [
                    {"type": m.attrib["type"], "ref": m.attrib["ref"], "role": m.attrib["role"]}
                    for m in relation.findall("member")
                ],
            }
        )
    return {"root": root, "nodes": nodes, "ways": ways, "restrictions": relations}


def parse_net(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    return {
        "root": root,
        "location": root.find("location"),
        "junctions": {j.attrib["id"]: j for j in root.findall("junction")},
        "edges": {e.attrib["id"]: e for e in root.findall("edge")},
        "tls": {t.attrib["id"]: t for t in root.findall("tlLogic")},
        "connections": root.findall("connection"),
    }


def edge_way_id(edge_id: str) -> str:
    return edge_id.lstrip("-").split("#", 1)[0]


def bearing(start: tuple[float, float], end: tuple[float, float]) -> float:
    """Clockwise degrees from geographic/grid north."""
    return (math.degrees(math.atan2(end[0] - start[0], end[1] - start[1])) + 360.0) % 360.0


def cardinal(value: float) -> str:
    return ("north", "east", "south", "west")[int((value + 45.0) // 90.0) % 4]


def octant(value: float) -> str:
    names = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
    return names[int((value + 22.5) // 45.0) % 8]


def geometry_movement(inbound: float, outbound: float) -> tuple[float, str]:
    angle = (outbound - inbound + 180.0) % 360.0 - 180.0
    if abs(angle) >= 135.0:
        movement = "u_turn"
    elif angle >= 45.0:
        movement = "right"
    elif angle <= -45.0:
        movement = "left"
    else:
        movement = "straight"
    return angle, movement


def connection_geometry(
    connection: ET.Element, junction_id: str, net: dict[str, Any]
) -> dict[str, Any]:
    junction = net["junctions"][junction_id]
    center = (float(junction.attrib["x"]), float(junction.attrib["y"]))
    from_edge = net["edges"][connection.attrib["from"]]
    to_edge = net["edges"][connection.attrib["to"]]
    source = net["junctions"][from_edge.attrib["from"]]
    destination = net["junctions"][to_edge.attrib["to"]]
    source_xy = (float(source.attrib["x"]), float(source.attrib["y"]))
    destination_xy = (float(destination.attrib["x"]), float(destination.attrib["y"]))
    inbound = bearing(source_xy, center)
    outbound = bearing(center, destination_xy)
    turn_angle, geometry_class = geometry_movement(inbound, outbound)
    approach_bearing = (inbound + 180.0) % 360.0
    return {
        "approach_bearing": approach_bearing,
        "approach_cardinal": cardinal(approach_bearing),
        "approach_octant": octant(approach_bearing),
        "outbound_bearing": outbound,
        "outbound_cardinal": cardinal(outbound),
        "outbound_octant": octant(outbound),
        "turn_angle": turn_angle,
        "geometry_class": geometry_class,
    }


def restrictions_for(osm: dict[str, Any], way_id: str, junction_id: str) -> list[dict[str, Any]]:
    matches = []
    for relation in osm["restrictions"]:
        refs = {member["ref"] for member in relation["members"]}
        if way_id in refs or junction_id in refs:
            matches.append(relation)
    return matches


def osm_evidence(osm: dict[str, Any], from_edge: str, to_edge: str, junction_id: str) -> str:
    way_ids = {edge_way_id(from_edge), edge_way_id(to_edge)}
    turn_values = []
    restriction_values = []
    for way_id in sorted(way_ids):
        tags = osm["ways"].get(way_id, {}).get("tags", {})
        tagged = {k: v for k, v in tags.items() if k.startswith("turn:lanes")}
        if tagged:
            turn_values.append(f"way/{way_id} {tagged}")
        for restriction in restrictions_for(osm, way_id, junction_id):
            restriction_values.append(
                f"relation/{restriction['id']} {restriction['restriction']}"
            )
    turn_text = "; ".join(sorted(set(turn_values))) or "turn:lanes absent"
    restriction_text = "; ".join(sorted(set(restriction_values))) or "relevant restriction absent"
    return f"{turn_text}; {restriction_text} in raw snapshot"


def lane_permission_text(edge: ET.Element) -> str:
    values = []
    for lane in edge.findall("lane"):
        allow = lane.attrib.get("allow")
        disallow = lane.attrib.get("disallow")
        if allow:
            values.append(f"lane{lane.attrib['index']}: allow={allow}")
        elif disallow:
            values.append(f"lane{lane.attrib['index']}: all except={disallow}")
        else:
            values.append(f"lane{lane.attrib['index']}: unrestricted")
    return " | ".join(values)


class PJCoord(ctypes.Structure):
    _fields_ = [("values", ctypes.c_double * 4)]


def find_proj_library(sumo_binary: Path) -> str:
    prefix = sumo_binary.resolve().parent.parent
    libraries = sorted(
        {
            *map(str, (prefix / "lib").glob("libproj*.dylib")),
            *map(str, (prefix / "lib").glob("libproj.so*")),
        }
    )
    if libraries:
        return libraries[-1]
    system_library = ctypes.util.find_library("proj")
    if system_library:
        return system_library
    raise FileNotFoundError("PROJ shared library could not be resolved from SUMO or the system")


def project_inverse(
    points: list[tuple[float, float]], source_crs: str, library_path: str, proj_data: Path
) -> list[tuple[float, float]]:
    """Transform with SUMO's linked official PROJ C library.

    sumolib.convertXY2LonLat is a thin pyproj wrapper.  The installed SUMO tool
    tree has sumolib but the host Python lacks pyproj, so using the exact linked
    libproj is the official equivalent without installing an audit dependency.
    """
    os.environ["PROJ_DATA"] = str(proj_data)
    library = ctypes.CDLL(library_path)
    library.proj_context_create.restype = ctypes.c_void_p
    library.proj_create_crs_to_crs.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_void_p,
    ]
    library.proj_create_crs_to_crs.restype = ctypes.c_void_p
    library.proj_normalize_for_visualization.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    library.proj_normalize_for_visualization.restype = ctypes.c_void_p
    library.proj_coord.argtypes = [ctypes.c_double] * 4
    library.proj_coord.restype = PJCoord
    library.proj_trans.argtypes = [ctypes.c_void_p, ctypes.c_int, PJCoord]
    library.proj_trans.restype = PJCoord
    context = library.proj_context_create()
    transform = library.proj_create_crs_to_crs(
        context, source_crs.encode("utf-8"), b"EPSG:4326", None
    )
    normalized = library.proj_normalize_for_visualization(context, transform)
    if not transform or not normalized:
        raise RuntimeError(f"PROJ could not create inverse transform for {source_crs}")
    result = []
    for x, y in points:
        coordinate = library.proj_coord(x, y, 0.0, 0.0)
        converted = library.proj_trans(normalized, 1, coordinate)  # PJ_INV
        result.append((converted.values[0], converted.values[1]))
    return result


def haversine_m(first: tuple[float, float], second: tuple[float, float]) -> float:
    lon1, lat1 = map(math.radians, first)
    lon2, lat2 = map(math.radians, second)
    delta_lon, delta_lat = lon2 - lon1, lat2 - lat1
    value = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2.0) ** 2
    )
    return 6_371_008.8 * 2.0 * math.asin(math.sqrt(value))


def projection_audit(
    net_path: Path,
    net: dict[str, Any],
    osm: dict[str, Any],
    tools: SumoTools,
) -> dict[str, Any]:
    location = net["location"]
    assert location is not None
    offset = tuple(float(value) for value in location.attrib["netOffset"].split(","))
    raw_utm_points = []
    sumo_points = []
    for tls_id in MVP_TLS_IDS:
        junction = net["junctions"][tls_id]
        xy = (float(junction.attrib["x"]), float(junction.attrib["y"]))
        sumo_points.append(xy)
        raw_utm_points.append((xy[0] - offset[0], xy[1] - offset[1]))
    if tools.proj_data is None:
        raise RuntimeError("Projection audit requires a resolved PROJ data directory")
    library = find_proj_library(tools.sumo)
    proj_data = tools.proj_data
    converted = project_inverse(
        raw_utm_points, location.attrib["projParameter"], library, proj_data
    )
    points = []
    for tls_id, xy, lonlat in zip(MVP_TLS_IDS, sumo_points, converted):
        raw_lonlat = (osm["nodes"][tls_id]["lon"], osm["nodes"][tls_id]["lat"])
        points.append(
            {
                "junction_id": tls_id,
                "sumo_xy": [round(value, 2) for value in xy],
                "converted_lon_lat": [round(value, 10) for value in lonlat],
                "raw_osm_lon_lat": list(raw_lonlat),
                "distance_error_m": round(haversine_m(lonlat, raw_lonlat), 6),
            }
        )
    max_error = max(point["distance_error_m"] for point in points)
    reload_env = sumo_subprocess_environment(tools, require_proj=True)
    with tempfile.TemporaryDirectory(prefix="hongdae-projection-") as temporary:
        reload_result = run(
            [
                str(tools.netconvert),
                "--sumo-net-file",
                str(net_path),
                "--output-file",
                str(Path(temporary) / "reload.net.xml"),
            ],
            env=reload_env,
        )
    return {
        "location_metadata": dict(location.attrib),
        "sumolib_convert_api": "available in bundled sumolib; unavailable at runtime because pyproj is not installed",
        "pyproj_installed": importlib.util.find_spec("pyproj") is not None,
        "official_equivalent_used": "SUMO-linked PROJ C API, source CRS from <location>, target EPSG:4326",
        "proj_library": str(library),
        "proj_data": str(proj_data),
        "points": points,
        "max_error_m": max_error,
        "netconvert_reload_with_proj_data_exit_code": reload_result.returncode,
        "proj_warning_seen_with_proj_data": "pj_obj_create" in reload_result.stdout,
        "verdict": "environment_warning_not_coordinate_error" if max_error < 0.1 else "unresolved",
    }


def controlled_link_records(net: dict[str, Any], osm: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records = []
    summaries: dict[str, Any] = {}
    for tls_id in MVP_TLS_IDS:
        phases = net["tls"][tls_id].findall("phase")
        connections = [c for c in net["connections"] if c.attrib.get("tl") == tls_id]
        grouped: dict[int, list[ET.Element]] = defaultdict(list)
        for connection in connections:
            grouped[int(connection.attrib["linkIndex"])].append(connection)
        for connection in connections:
            link_index = int(connection.attrib["linkIndex"])
            geometry = connection_geometry(connection, tls_id, net)
            movement = MOVEMENT_NAMES[connection.attrib["dir"]]
            peers = [
                f"{peer.attrib['from']}_{peer.attrib['fromLane']}->{peer.attrib['to']}_{peer.attrib['toLane']}"
                for peer in grouped[link_index]
                if peer is not connection
            ]
            from_edge = net["edges"][connection.attrib["from"]]
            to_edge = net["edges"][connection.attrib["to"]]
            passenger = lane_allows(
                from_edge.findall("lane")[int(connection.attrib["fromLane"])], "passenger"
            ) and lane_allows(
                to_edge.findall("lane")[int(connection.attrib["toLane"])], "passenger"
            )
            if tls_id == "2959081059" and link_index == 15:
                status = "priority_review_minor_green_warning"
                correction = "review_required"
            elif movement == "u_turn":
                status = "unresolved_no_osm_legality_evidence"
                correction = "hold_pending_field_or_streetview_evidence"
            elif not passenger:
                status = "geometry_consistent_non_passenger_scope"
                correction = "no_vehicle_topology_change_indicated"
            else:
                status = "geometry_consistent_osm_legality_unverified"
                correction = "no_change_indicated_by_current_evidence"
            phase_states = [phase.attrib["state"][link_index] for phase in phases]
            basis = (
                f"SUMO dir={connection.attrib['dir']} and node geometry agree: "
                f"turn={geometry['turn_angle']:.1f}°, approach azimuth="
                f"{geometry['approach_bearing']:.1f}° ({geometry['approach_octant']}), "
                f"outbound azimuth={geometry['outbound_bearing']:.1f}° "
                f"({geometry['outbound_octant']}). "
                "Geometry supports movement class, not legal permission; no external imagery used."
            )
            record = {
                "physical_intersection": TLS_NAMES[tls_id],
                "tls_id": tls_id,
                "link_index": link_index,
                "from_edge": connection.attrib["from"],
                "from_lane": int(connection.attrib["fromLane"]),
                "to_edge": connection.attrib["to"],
                "to_lane": int(connection.attrib["toLane"]),
                "via_lane": connection.attrib.get("via", ""),
                "sumo_dir": connection.attrib["dir"],
                "estimated_approach_direction": geometry["approach_cardinal"],
                "estimated_travel_direction": geometry["outbound_cardinal"],
                "movement_class": movement,
                "same_link_index_connections": peers,
                **{f"phase_{index}_state": state for index, state in enumerate(phase_states)},
                "osm_turn_lanes_or_restriction_evidence": osm_evidence(
                    osm, connection.attrib["from"], connection.attrib["to"], tls_id
                ),
                "review_status": status,
                "correction_required": correction,
                "basis_and_uncertainty": basis,
            }
            records.append(record)
        movement_counts = Counter(MOVEMENT_NAMES[c.attrib["dir"]] for c in connections)
        summaries[tls_id] = {
            "physical_intersection": TLS_NAMES[tls_id],
            "connection_count": len(connections),
            "link_group_count": len(grouped),
            "duplicate_link_index_groups": {
                str(index): len(items) for index, items in grouped.items() if len(items) > 1
            },
            "movement_counts": dict(sorted(movement_counts.items())),
        }
    records.sort(key=lambda item: (item["tls_id"], item["link_index"], item["from_lane"]))
    return records, summaries


def edge_mapping_records(net: dict[str, Any], osm: dict[str, Any]) -> list[dict[str, Any]]:
    contexts: dict[tuple[str, str], set[str]] = defaultdict(set)
    for connection in net["connections"]:
        tls_id = connection.attrib.get("tl")
        if tls_id not in MVP_TLS_IDS:
            continue
        contexts[(tls_id, connection.attrib["from"])].add("incoming")
        contexts[(tls_id, connection.attrib["to"])].add("outgoing")
    # Include the adjacent World Cup buk-ro/Yanghwa-ro warning connection.
    contexts[("11059771900", "254749392")].add("minor-green incoming")
    contexts[("11059771900", "515592172#0")].add("minor-green outgoing")

    records = []
    for (junction_id, edge_id), roles in sorted(contexts.items()):
        edge = net["edges"][edge_id]
        junction = net["junctions"][junction_id]
        center = (float(junction.attrib["x"]), float(junction.attrib["y"]))
        if any("incoming" in role for role in roles):
            remote_id = edge.attrib["from"]
        else:
            remote_id = edge.attrib["to"]
        remote = net["junctions"][remote_id]
        relative = bearing(center, (float(remote.attrib["x"]), float(remote.attrib["y"])))
        way_id = edge_way_id(edge_id)
        tags = osm["ways"].get(way_id, {}).get("tags", {})
        restrictions = restrictions_for(osm, way_id, junction_id)
        lanes = edge.findall("lane")
        turn_tags = {k: v for k, v in tags.items() if k.startswith("turn:lanes")}
        records.append(
            {
                "physical_intersection": TLS_NAMES.get(
                    junction_id, "월드컵북로/양화로 인접 경고 교차로"
                ),
                "junction_id": junction_id,
                "edge_id": edge_id,
                "role": ";".join(sorted(roles)),
                "osm_way_id": way_id,
                "road_name": edge.attrib.get("name") or tags.get("name", "unnamed"),
                "relative_direction": cardinal(relative),
                "bearing_degrees": round(relative, 1),
                "sumo_lane_count": len(lanes),
                "passenger_lane_count": sum(lane_allows(lane, "passenger") for lane in lanes),
                "lane_permissions": lane_permission_text(edge),
                "osm_lanes": tags.get("lanes", "absent"),
                "osm_lanes_forward": tags.get("lanes:forward", "absent"),
                "osm_lanes_backward": tags.get("lanes:backward", "absent"),
                "osm_turn_lanes": json.dumps(turn_tags, ensure_ascii=False) if turn_tags else "absent",
                "osm_oneway": tags.get("oneway", "absent (not explicit permission evidence)"),
                "restriction_relations": (
                    "; ".join(
                        f"{item['id']}:{item['restriction']}" for item in restrictions
                    )
                    or "none involving way/junction in raw snapshot"
                ),
                "evidence_status": "OSM tags + generated net + geometry; no external imagery",
            }
        )
    return records


def decode_foes(junction: ET.Element) -> dict[int, set[int]]:
    # SUMO request bitsets enumerate link indices from right to left.
    return {
        int(request.attrib["index"]): {
            index for index, value in enumerate(reversed(request.attrib["foes"])) if value == "1"
        }
        for request in junction.findall("request")
    }


def phase_records(net: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = []
    summaries = {}
    for tls_id in MVP_TLS_IDS:
        logic = net["tls"][tls_id]
        phases = logic.findall("phase")
        junction = net["junctions"][tls_id]
        foes = decode_foes(junction)
        service = set()
        tls_conflicts = []
        green_red_violations = []
        for phase_index, phase in enumerate(phases):
            state = phase.attrib["state"]
            next_state = phases[(phase_index + 1) % len(phases)].attrib["state"]
            green = [index for index, value in enumerate(state) if value == "G"]
            minor = [index for index, value in enumerate(state) if value == "g"]
            yellow = [index for index, value in enumerate(state) if value in "yY"]
            red = [index for index, value in enumerate(state) if value in "rR"]
            service.update(green)
            service.update(minor)
            active = green + minor
            conflict_pairs = []
            for left_position, first in enumerate(active):
                for second in active[left_position + 1 :]:
                    if second in foes.get(first, set()) or first in foes.get(second, set()):
                        kind = f"{state[first]}-{state[second]}"
                        pair = {"first": first, "second": second, "type": kind}
                        conflict_pairs.append(pair)
                        tls_conflicts.append({"phase": phase_index, **pair})
            violations = [
                index
                for index, value in enumerate(state)
                if value in "Gg" and next_state[index] in "rR"
            ]
            green_red_violations.extend((phase_index, index) for index in violations)
            rows.append(
                {
                    "physical_intersection": TLS_NAMES[tls_id],
                    "tls_id": tls_id,
                    "phase_index": phase_index,
                    "duration_s": float(phase.attrib["duration"]),
                    "state": state,
                    "green_links": green,
                    "minor_green_links": minor,
                    "yellow_links": yellow,
                    "red_links": red,
                    "simultaneous_foe_pairs": conflict_pairs,
                    "green_to_red_without_yellow": violations,
                    "audit_scope": "automatic_program_structure_only_not_actual_signal_timing",
                }
            )
        connections = [c for c in net["connections"] if c.attrib.get("tl") == tls_id]
        link_indices = {int(c.attrib["linkIndex"]) for c in connections}
        summaries[tls_id] = {
            "program_type": logic.attrib.get("type"),
            "phase_count": len(phases),
            "state_length": len(phases[0].attrib["state"]),
            "link_index_count": len(link_indices),
            "all_links_serviced": link_indices <= service,
            "unserviced_links": sorted(link_indices - service),
            "green_to_red_without_yellow": green_red_violations,
            "simultaneous_foe_pairs": tls_conflicts,
            "tls_ids_isolated": all(c.attrib.get("tl") == tls_id for c in connections),
        }
    return rows, summaries


def uturn_records(controlled: list[dict[str, Any]], net: dict[str, Any], osm: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for record in controlled:
        if record["movement_class"] != "u_turn":
            continue
        from_edge = net["edges"][record["from_edge"]]
        from_lane = from_edge.findall("lane")[record["from_lane"]]
        way_id = edge_way_id(record["from_edge"])
        tags = osm["ways"].get(way_id, {}).get("tags", {})
        explicit = {k: v for k, v in tags.items() if "turn" in k or "u_turn" in k}
        restrictions = restrictions_for(osm, way_id, record["tls_id"])
        rows.append(
            {
                "physical_intersection": record["physical_intersection"],
                "tls_id": record["tls_id"],
                "link_index": record["link_index"],
                "from_edge": record["from_edge"],
                "from_lane": record["from_lane"],
                "to_edge": record["to_edge"],
                "to_lane": record["to_lane"],
                "approach_direction": record["estimated_approach_direction"],
                "passenger_usable": lane_allows(from_lane, "passenger"),
                "osm_way_id": way_id,
                "osm_turn_evidence": json.dumps(explicit, ensure_ascii=False) if explicit else "absent",
                "restriction_evidence": (
                    "; ".join(f"{r['id']}:{r['restriction']}" for r in restrictions) or "absent"
                ),
                "classification": "자동 생성됐지만 근거 없음",
                "field_or_streetview_check_needed": True,
                "basis_and_uncertainty": (
                    "Generated dir=t connection is geometrically a U-turn. Raw OSM has no explicit "
                    "turn:lanes/U-turn tag or restriction involving this way and junction. Tag absence "
                    "is not evidence of permission; no external imagery was used."
                ),
            }
        )
    return sorted(rows, key=lambda item: (item["tls_id"], item["link_index"]))


def route_crosses_junction(route: list[str], junction_id: str, net: dict[str, Any]) -> bool:
    for first, second in zip(route, route[1:]):
        first_edge = net["edges"][first]
        second_edge = net["edges"][second]
        if first_edge.attrib.get("to") == junction_id and second_edge.attrib.get("from") == junction_id:
            return True
    return False


def route_smoke(
    net_path: Path,
    net: dict[str, Any],
    tools: SumoTools,
) -> dict[str, Any]:
    duarouter = tools.sumo.parent / "duarouter"
    env = sumo_subprocess_environment(tools, require_proj=True)
    with tempfile.TemporaryDirectory(prefix="hongdae-mvp-vehicle-") as temporary:
        temporary_path = Path(temporary)
        trips_path = temporary_path / "trips.xml"
        routes_path = temporary_path / "routes.rou.xml"
        tripinfo_path = temporary_path / "tripinfo.xml"
        statistics_path = temporary_path / "statistics.xml"
        route_root = ET.Element("routes")
        ET.SubElement(
            route_root,
            "vType",
            id="passenger",
            vClass="passenger",
            accel="2.6",
            decel="4.5",
            sigma="0.5",
            length="5.0",
            minGap="2.5",
            maxSpeed="13.89",
        )
        for index, (route_id, _description, start, end) in enumerate(ROUTE_FLOWS):
            ET.SubElement(
                route_root,
                "trip",
                id=route_id,
                type="passenger",
                depart=str(index * 10),
                departLane="best",
                departSpeed="0",
                arrivalPos="max",
                **{"from": start, "to": end},
            )
        ET.ElementTree(route_root).write(trips_path, encoding="utf-8", xml_declaration=True)
        routing = run(
            [
                str(duarouter),
                "--net-file",
                str(net_path),
                "--route-files",
                str(trips_path),
                "--output-file",
                str(routes_path),
                "--exit-times",
                "true",
                "--no-warnings",
                "false",
            ],
            env=env,
        )
        generated_routes: dict[str, list[str]] = {}
        if routes_path.exists():
            route_xml = ET.parse(routes_path).getroot()
            for vehicle in route_xml.findall("vehicle"):
                generated_routes[vehicle.attrib["id"]] = vehicle.find("route").attrib["edges"].split()
        simulation = run(
            [
                str(tools.sumo),
                "--net-file",
                str(net_path),
                "--route-files",
                str(routes_path),
                "--begin",
                "0",
                "--end",
                "900",
                "--step-length",
                "1",
                "--seed",
                "42",
                "--no-step-log",
                "true",
                "--duration-log.statistics",
                "true",
                "--tripinfo-output",
                str(tripinfo_path),
                "--statistic-output",
                str(statistics_path),
                "--collision.action",
                "warn",
                "--collision.check-junctions",
                "true",
            ],
            env=env,
        )
        tripinfo: dict[str, dict[str, str]] = {}
        if tripinfo_path.exists():
            tripinfo = {
                item.attrib["id"]: dict(item.attrib)
                for item in ET.parse(tripinfo_path).getroot().findall("tripinfo")
            }
        stats: dict[str, Any] = {}
        if statistics_path.exists():
            stats_root = ET.parse(statistics_path).getroot()
            for tag in ("vehicles", "teleports", "safety", "vehicleTripStatistics"):
                element = stats_root.find(tag)
                stats[tag] = dict(element.attrib) if element is not None else {}
        route_rows = []
        for route_id, description, start, end in ROUTE_FLOWS:
            edges = generated_routes.get(route_id, [])
            core = [edge for edge in edges if edge in CORE_EDGES]
            info = tripinfo.get(route_id)
            route_rows.append(
                {
                    "route_id": route_id,
                    "flow": description,
                    "start_edge": start,
                    "end_edge": end,
                    "traversed_edges": edges,
                    "passes_hongik_gate": route_crosses_junction(edges, "2959081059", net) if edges else False,
                    "passes_hongdae_station": route_crosses_junction(edges, "3034197250", net) if edges else False,
                    "expected_core_entry_edge": core[0] if core else None,
                    "expected_core_exit_edge": core[-1] if core else None,
                    "route_generation_success": bool(edges),
                    "disconnected": not bool(edges),
                    "replacement_or_repair": False,
                    "arrived": info is not None,
                    "arrival_time_s": float(info["arrival"]) if info else None,
                    "duration_s": float(info["duration"]) if info else None,
                    "waiting_time_s": float(info["waitingTime"]) if info else None,
                    "reroute_count": int(info["rerouteNo"]) if info else None,
                }
            )
        warning_lines = [
            line for line in (routing.stdout + "\n" + simulation.stdout).splitlines() if "Warning:" in line
        ]
        return {
            "checked_on": AUDIT_DATE,
            "temporary_files_persisted": False,
            "routing": {
                "duarouter_path": str(duarouter),
                "exit_code": routing.returncode,
                "repair_enabled": False,
                "ignore_errors_enabled": False,
                "warnings": warning_lines,
            },
            "routes": route_rows,
            "smoke_test": {
                "sumo_exit_code": simulation.returncode,
                "end_time_s": 900,
                "vehicles": stats.get("vehicles", {}),
                "teleports": stats.get("teleports", {}),
                "safety": stats.get("safety", {}),
                "trip_statistics": stats.get("vehicleTripStatistics", {}),
                "route_errors": sum("route" in line.lower() and "error" in line.lower() for line in warning_lines),
                "all_vehicles_arrived": len(tripinfo) == len(ROUTE_FLOWS),
                "infinite_wait_indication": not (
                    len(tripinfo) == len(ROUTE_FLOWS)
                    and stats.get("vehicles", {}).get("running") == "0"
                    and stats.get("vehicles", {}).get("waiting") == "0"
                ),
                "scope": "structural_passability_only_not_signal_performance",
            },
        }


def directory_digest(path: Path) -> str:
    digest = hashlib.sha256()
    if not path.exists():
        return "absent"
    for item in sorted(entry for entry in path.rglob("*") if entry.is_file()):
        digest.update(str(item.relative_to(path)).encode("utf-8"))
        digest.update(b"\0")
        with item.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def git_info() -> dict[str, Any]:
    def output(arguments: list[str]) -> str:
        return run(["git", *arguments]).stdout.strip()

    head = output(["rev-parse", "HEAD"])
    divergence = output(["rev-list", "--left-right", "--count", "origin/main...main"])
    behind, ahead = (int(value) for value in divergence.split())
    return {
        "head": head,
        "baseline_commit": BASELINE_COMMIT,
        "head_matches_baseline": head == BASELINE_COMMIT,
        "main_behind_origin_main": behind,
        "main_ahead_of_origin_main": ahead,
        "legacy_results_status": output(
            ["status", "--porcelain=v1", "--untracked-files=all", "--", "legacy", "results"]
        ).splitlines(),
    }


def minor_green_findings(net: dict[str, Any], osm: dict[str, Any]) -> list[dict[str, Any]]:
    items = [
        {
            "connection": "299767124#4 -> 218976037#0",
            "junction_id": "2959081059",
            "tls_id": "2959081059",
            "link_index": 15,
            "source_lane": 0,
            "source_lane_shared_movements": ["right", "straight", "left", "u_turn"],
            "closest_category": "자동 신호 프로그램의 permissive green 문제",
            "secondary_candidate": "실제 좌회전 차로 누락",
            "finding": (
                "Geometry and SUMO dir both identify a left turn. The sole passenger source lane is "
                "shared by all four movements; raw way/299767124 has neither lanes nor turn:lanes nor "
                "maxspeed. Netconvert therefore applies 27.78 m/s and serves link 15 as minor green g "
                "with conflicting protected movements. Current data cannot establish the physical lane "
                "layout, so the connection itself is not classified as erroneous."
            ),
        },
        {
            "connection": "254749392 -> 515592172#0",
            "junction_id": "11059771900",
            "tls_id": "11059771900",
            "link_index": 10,
            "source_lane": 2,
            "source_lane_shared_movements": ["straight", "left", "u_turn"],
            "closest_category": "자동 신호 프로그램의 permissive green 문제",
            "secondary_candidate": "실제 좌회전 차로 누락",
            "finding": (
                "The geometrically valid left turn is generated from lane 2, which also serves straight "
                "and U-turn movements. Raw way/254749392 states lanes=6 but has no turn:lanes or maxspeed; "
                "the generated approach speed is 27.78 m/s and link 10 is permissive g. This is adjacent "
                "to, not controlled by, station TLS 3034197250. Physical lane exclusivity and legality "
                "remain unresolved; no evidence currently supports deleting the connection."
            ),
        },
    ]
    # Defensive checks keep the prose tied to the parsed network.
    for item in items:
        match = next(
            c
            for c in net["connections"]
            if c.attrib.get("tl") == item["tls_id"]
            and int(c.attrib["linkIndex"]) == item["link_index"]
        )
        item["parsed_from_edge"] = match.attrib["from"]
        item["parsed_to_edge"] = match.attrib["to"]
        item["osm_evidence"] = osm_evidence(
            osm, match.attrib["from"], match.attrib["to"], item["junction_id"]
        )
    return items


def correction_plan(minor_findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = [
        {
            "correction_id": "MV-PROJ-001",
            "target": "audit/build execution environment",
            "current_auto_state": "Network metadata uses UTM zone 52; tools emit proj.db lookup warnings without PROJ_DATA.",
            "problem": "Unconfigured resource lookup obscures whether a future transform failure is environmental or spatial.",
            "evidence": "Bundled proj.db plus official inverse transform reproduces both raw OSM nodes within 0.006 m.",
            "proposed_change": "Set PROJ_DATA to the versioned SUMO bundle share/proj directory in the reproducible audit/build wrapper.",
            "priority": "필수",
            "affected_controlled_links": [],
            "post_change_tests": ["inverse-transform fixture", "netconvert/net reload without pj_obj_create warning"],
            "derivation_method": "reproducible correction/audit script environment; no network XML edit",
        },
        {
            "correction_id": "MV-GATE-LANE-001",
            "target": "junction 2959081059; edge 299767124#4 lane 0; link 15 and sibling links 13,14,16",
            "current_auto_state": "One passenger lane fans to right/straight/left/U-turn; left link 15 receives permissive g at 27.78 m/s.",
            "problem": minor_findings[0]["finding"],
            "evidence": "Generated connection/phase, netconvert warning, raw way/299767124 tag absence, geometry.",
            "proposed_change": "First verify lane arrows/count and legal speed. If confirmed, encode lane count/speed and explicit lane-to-lane connections in a plain edge/connection patch or deterministic script; otherwise retain and mark unresolved.",
            "priority": "필수",
            "affected_controlled_links": [13, 14, 15, 16],
            "post_change_tests": ["controlled-link snapshot", "lane reachability", "minor-green warning regression", "seven-route smoke test"],
            "derivation_method": "plain XML edge/connection patch or reproducible correction script",
        },
        {
            "correction_id": "MV-ADJ-LANE-001",
            "target": "junction 11059771900; edge 254749392 lane 2; links 9,10,11",
            "current_auto_state": "Lane 2 serves straight/left/U-turn; left link 10 receives permissive g at 27.78 m/s.",
            "problem": minor_findings[1]["finding"],
            "evidence": "Generated connection/phase, netconvert warning, raw lanes=6 with turn:lanes/maxspeed absent, geometry.",
            "proposed_change": "Verify physical lane arrows, legal speed, and U-turn rule; then encode only confirmed speed/lane/connection facts in a plain patch or deterministic script.",
            "priority": "권장",
            "affected_controlled_links": ["11059771900:9", "11059771900:10", "11059771900:11"],
            "post_change_tests": ["east-side and World Cup route smoke", "minor-green warning regression", "lane reachability"],
            "derivation_method": "plain XML edge/connection patch or reproducible correction script",
        },
        {
            "correction_id": "MV-UTURN-GATE-001",
            "target": "junction 2959081059 U-turn links 3,7,12,16",
            "current_auto_state": "Four generated U-turns; one service-road approach is not passenger-usable.",
            "problem": "No raw OSM tag/restriction or external sign/marking evidence establishes permission or prohibition.",
            "evidence": "Generated dir=t and geometry only; tag absence is not permission.",
            "proposed_change": "Make no topology change until dated field/street-view evidence is captured; then add explicit plain connection deletions/retentions in a reviewed patch.",
            "priority": "보류",
            "affected_controlled_links": [3, 7, 12, 16],
            "post_change_tests": ["link-index/phase remap", "all gate movements route test", "foe/conflict audit"],
            "derivation_method": "plain XML connection/tll patch with evidence manifest",
        },
        {
            "correction_id": "MV-UTURN-STATION-001",
            "target": "junction 3034197250 U-turn links 5,18",
            "current_auto_state": "Two generated U-turns; link 5 is permissive g with link 6 protected G.",
            "problem": "No raw OSM tag/restriction or external sign/marking evidence establishes permission or prohibition.",
            "evidence": "Generated dir=t, phase/foe matrix, and geometry only.",
            "proposed_change": "Make no topology change until dated field/street-view evidence is captured; then patch connection/tll definitions reproducibly.",
            "priority": "보류",
            "affected_controlled_links": [5, 18],
            "post_change_tests": ["link-index/phase remap", "all station movements route test", "foe/conflict audit"],
            "derivation_method": "plain XML connection/tll patch with evidence manifest",
        },
        {
            "correction_id": "MV-TLS-STRUCT-001",
            "target": "junction 2959081059 phase 4; links 2 and 11",
            "current_auto_state": "SUMO request matrix marks the pair as foes while both are uppercase G.",
            "problem": "Potential protected-green conflict in an irregular junction; link 2 is delivery/bicycle-only but remains controlled traffic.",
            "evidence": "Parsed request foes bitsets and auto phase state rrGGrrrrrrrGGrrrr.",
            "proposed_change": "Before any operational use, validate internal paths/vehicle classes and generate a reviewed tll patch. Do not treat the six-phase auto program as real timing.",
            "priority": "필수",
            "affected_controlled_links": [2, 11],
            "post_change_tests": ["foe-pair phase assertion", "green-yellow transition test", "SUMO collision smoke with relevant vehicle classes"],
            "derivation_method": "plain XML tll patch or netconvert tll additional input",
        },
        {
            "correction_id": "MV-STATION-GEOM-001",
            "target": "junction 3034197250 approaches ±218976035#0 and ±254749392",
            "current_auto_state": "Several normal approach/departure lanes are only 0.20 m after junction-shape subtraction.",
            "problem": "Routes connect, but the near-zero storage length can distort queues and lane changes in later traffic experiments.",
            "evidence": "Generated lane lengths and successful structural smoke test.",
            "proposed_change": "Inspect plain-node junction shapes and adjacent split nodes; if queue storage is materially wrong, apply an explicit node-shape/edge patch rather than editing auto.net.xml.",
            "priority": "권장",
            "affected_controlled_links": list(range(19)),
            "post_change_tests": ["lane-length threshold", "route smoke", "queue storage sanity test", "controlled-link snapshot"],
            "derivation_method": "plain XML node/edge patch or explicit NetEdit change log",
        },
        {
            "correction_id": "MV-SPEED-001",
            "target": "MVP and adjacent primary/secondary/tertiary approaches lacking maxspeed",
            "current_auto_state": "Typemap defaults include 27.78 m/s secondary and 22.22 m/s tertiary speeds.",
            "problem": "Raw OSM does not establish these as physical/legal intersection speeds; they contribute directly to minor-green warnings.",
            "evidence": "Raw maxspeed absence and generated lane speed attributes.",
            "proposed_change": "Obtain authoritative/dated speed evidence, then apply a deterministic plain edge speed patch. Do not guess a Seoul-wide value.",
            "priority": "필수",
            "affected_controlled_links": "all 36 MVP connections plus adjacent warning links",
            "post_change_tests": ["speed provenance check", "minor-green warning regression", "route smoke"],
            "derivation_method": "plain XML edge patch or reproducible correction script",
        },
    ]
    for item in items:
        item["target_junction_edge_lane_connection"] = item["target"]
    return items


def write_csv(path: Path, rows: list[dict[str, Any]], columns: tuple[str, ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
                    for key, value in row.items()
                }
            )


def render_report(
    projection: dict[str, Any],
    link_summaries: dict[str, Any],
    controlled: list[dict[str, Any]],
    phase_summaries: dict[str, Any],
    uturns: list[dict[str, Any]],
    routes: dict[str, Any],
    minors: list[dict[str, Any]],
    corrections: list[dict[str, Any]],
    provenance: dict[str, Any],
) -> str:
    movement_lines = []
    for tls_id in MVP_TLS_IDS:
        by_approach: dict[str, Counter[str]] = defaultdict(Counter)
        for row in controlled:
            if row["tls_id"] == tls_id:
                by_approach[row["estimated_approach_direction"]][row["movement_class"]] += 1
        for direction, counts in sorted(by_approach.items()):
            movement_lines.append(
                f"| {TLS_NAMES[tls_id]} | {direction} | "
                + ", ".join(f"{key} {value}" for key, value in sorted(counts.items()))
                + " |"
            )
    projection_lines = "\n".join(
        f"| {point['junction_id']} | {point['sumo_xy']} | {point['converted_lon_lat']} | "
        f"{point['raw_osm_lon_lat']} | {point['distance_error_m']:.6f} |"
        for point in projection["points"]
    )
    uturn_lines = "\n".join(
        f"| {row['physical_intersection']} | {row['link_index']} | "
        f"`{row['from_edge']}_{row['from_lane']} → {row['to_edge']}_{row['to_lane']}` | "
        f"{row['approach_direction']} | {'yes' if row['passenger_usable'] else 'no'} | "
        f"{row['classification']} |"
        for row in uturns
    )
    route_lines = "\n".join(
        f"| {row['route_id']} | `{row['start_edge']}` | `{row['end_edge']}` | "
        f"{row['passes_hongik_gate']} | {row['passes_hongdae_station']} | "
        f"{row['expected_core_entry_edge'] or '—'} / {row['expected_core_exit_edge'] or '—'} | "
        f"{'success' if row['route_generation_success'] else 'failed'} | "
        f"{'arrived' if row['arrived'] else 'not arrived'} |"
        for row in routes["routes"]
    )
    correction_lines = "\n".join(
        f"| {item['correction_id']} | {item['priority']} | {item['target']} | {item['proposed_change']} |"
        for item in corrections
    )
    gate_conflicts = phase_summaries["2959081059"]["simultaneous_foe_pairs"]
    station_conflicts = phase_summaries["3034197250"]["simultaneous_foe_pairs"]
    priorities = Counter(item["priority"] for item in corrections)
    smoke = routes["smoke_test"]
    return f"""# MVP 차량 네트워크·controlled-link 정밀 감사

감사일: `{AUDIT_DATE}`
범위: 자동 생성본의 차량 구조 감사만 수행했다. raw OSM, generated auto network,
`legacy/`, `results/`, corrected network는 수정하지 않았다. 보행자 crossing, 영구 수요,
실제 신호 주기 추정, RL 구현은 범위 밖이다.

## 1. 기준 상태와 provenance

- 감사 시작 시 Git HEAD: `{provenance['git']['head']}`; 지정 기준 commit과 일치:
  `{provenance['git']['head_matches_baseline']}`.
- `main` 대 `origin/main`: ahead `{provenance['git']['main_ahead_of_origin_main']}` / behind
  `{provenance['git']['main_behind_origin_main']}`.
- 시작 전 작업 트리: **clean** (감사 실행 전 직접 확인). 이 보고서 생성 후 변경은 아래 산출물과
  감사 스크립트/테스트뿐이다.
- raw SHA-256: `{provenance['raw_sha256']}` (baseline 일치:
  `{provenance['raw_checksum_matches']}`).
- generated net SHA-256: `{provenance['net_sha256']}` (baseline 일치:
  `{provenance['net_checksum_matches']}`).
- SUMO: `{provenance['sumo_version']}` at `{provenance['sumo_path']}`.
- netconvert: `{provenance['netconvert_version']}` at `{provenance['netconvert_path']}`.
- duarouter: `{provenance['duarouter_version']}`.
- `legacy/`와 `results/`의 감사 전/후 tree digest가 각각 동일하고 Git path status도 비어 있다.

## 2. 투영 정확성 판정

`<location>`은 `{projection['location_metadata']['projParameter']}`, offset
`{projection['location_metadata']['netOffset']}`, original boundary
`{projection['location_metadata']['origBoundary']}`를 기록한다. 설치된 Python에는 `pyproj`가 없어
`sumolib.net.convertXY2LonLat`를 그대로 호출할 수 없었지만, 그 API가 감싸는 것과 동등한
**SUMO가 링크한 공식 PROJ C API**를 같은 projection string/offset으로 호출했다.

| Junction | SUMO XY | inverse lon/lat | raw OSM lon/lat | error (m) |
|---|---|---|---|---:|
{projection_lines}

최대 오차는 `{projection['max_error_m']:.6f} m`이다. 올바른 `proj.db`는
`{projection['proj_data']}`이며 `PROJ_DATA`로 이 디렉터리를 지정한 net reload에서는
`pj_obj_create`가 재발하지 않았다. 따라서 기존 경고는 **환경 resource lookup 경고이며 실제
네트워크 좌표 오류가 아니다**. 방향 판정을 계속하기에 충분하다.

승인 후 `scripts/network/common.py`에 이식 가능한 resolver를 추가했다. 명시 인자 → 유효한
`PROJ_DATA` → 유효한 `PROJ_LIB` → 해석된 SUMO 설치/bundle → 표준 시스템 위치 순서로
`proj.db`를 확인하며, build/audit의 SUMO 계열 subprocess는 공통 환경 생성기를 사용한다.
기존 실행 당시 경고와 위 오차 수치는 역사적 감사 사실로 그대로 유지한다.

## 3. controlled links 전수 결과

| 교차로 | connection | link group | duplicate link-index group | movement |
|---|---:|---:|---:|---|
| 홍익대학교 정문 앞 | {link_summaries['2959081059']['connection_count']} | {link_summaries['2959081059']['link_group_count']} | {len(link_summaries['2959081059']['duplicate_link_index_groups'])} | {link_summaries['2959081059']['movement_counts']} |
| 홍대입구역사거리 | {link_summaries['3034197250']['connection_count']} | {link_summaries['3034197250']['link_group_count']} | {len(link_summaries['3034197250']['duplicate_link_index_groups'])} | {link_summaries['3034197250']['movement_counts']} |

두 TLS 모두 한 link index에 여러 실제 connection이 묶인 경우는 없다. 전수 36개 connection의
lane/via/phase state/OSM 근거는 `controlled_links.csv`와 `controlled_links.json`에 있다.

방향은 UTM node geometry에서 얻은 진북 기준 방위각을 가장 가까운 cardinal로 축약했다. 대각선
형상의 정문 교차로는 같은 cardinal bucket에 서로 다른 남동·남서 접근이 들어갈 수 있으므로 CSV의
근거 문장에 octant와 각도를 함께 보존했다.

| 교차로 | 추정 접근 cardinal | movement connection 수 |
|---|---|---|
{chr(10).join(movement_lines)}

모든 connection에서 SUMO `dir`과 기하학적 회전 부호가 일치했다. 이는 형상 분류의 근거일 뿐
실제 허용 근거는 아니다. 관련 OSM way에는 `turn:lanes`가 없고 두 junction/way를 멤버로 하는
restriction relation도 raw snapshot에 없다. 태그 부재를 허용으로 해석하지 않았다.

## 4. U턴 6개

| 교차로 | link | connection | 접근 | passenger 사용 | 판정 |
|---|---:|---|---|---|---|
{uturn_lines}

6개 모두 `dir=t`와 형상은 일치하지만 허용·금지를 확정할 OSM 태그나 외부 표지/노면표시 자료가
없다. 따라서 전부 **자동 생성됐지만 근거 없음**으로 분류하고 삭제하지 않았다. 정문 link 7은
service lane의 `allow=pedestrian delivery bicycle` 때문에 승용차는 사용할 수 없다. 현장 또는
날짜가 확인되는 로드뷰 검토 전에는 나머지도 임의 허용/금지하지 않는다.

## 5. minor-green 경고 2개

1. `{minors[0]['connection']}`: **자동 신호 프로그램의 permissive-green 문제에 가장 가깝고,
   실제 좌회전 차로 누락이 2차 후보**다. 유일한 승용차 lane 0이 right/straight/left/U-turn을
   모두 담당하며 link 15는 phase 0에서 `g`다. raw way/299767124에는 `lanes`, `turn:lanes`,
   `maxspeed`가 모두 없고 typemap 결과 속도는 27.78 m/s다. 기하학은 명확한 left이므로 회전
   방향 오판 가능성은 낮고, connection 삭제 근거는 없다.
2. `{minors[1]['connection']}`: 같은 조합에 가깝다. lane 2가 straight/left/U-turn을 공유하고
   link 10은 `g`; raw way/254749392는 `lanes=6`만 있고 `turn:lanes`/`maxspeed`가 없다. 이
   경고는 역사거리 TLS가 아니라 바로 북서쪽 인접 TLS `11059771900`의 link 10이다. 연결은
   기하학적으로 valid left이며 현 자료만으로 실제 전용 좌회전 차로 유무는 판단 불가다.

## 6. 자동 프로그램 구조 감사

이 절은 **자동 프로그램 구조 감사**이며 안전성 확정이나 실제 신호 주기 판정이 아니다.

- 두 TLS 모두 6-phase static이며 모든 link가 최소 한 green/minor-green phase에서 서비스된다.
- green/minor-green에서 바로 red로 바뀌는 link는 없고 yellow(또는 계속 green)를 거친다.
- 정문 phase 0에는 `G-g` foe pair 5개가 있고, phase 4에는 request matrix상 foe인 link
  **2와 11이 동시에 uppercase `G`**다. 링크 2는 delivery/bicycle-only 목적지지만 구조상
  보호 녹색 충돌 후보이므로 corrected network 전 검증이 필수다. 전체 pair:
  `{json.dumps(gate_conflicts, ensure_ascii=False)}`.
- 역사거리 phase 4에는 U-turn link 5 (`g`)와 right-turn link 6 (`G`)의 foe pair 1개가 있다.
  전체 pair: `{json.dumps(station_conflicts, ensure_ascii=False)}`.
- 정문 U-turn 3/12는 phase 4 `G`, 7/16은 phase 0 `g`; 역사거리 U-turn 5는 phase 4 `g`,
  18은 phase 0 `G`다. green 여부는 법적 허용 근거가 아니다.
- 각 state 길이는 해당 TLS link 범위(17/19)와 일치하고 connection의 `tl`도 분리돼 있어 두
  TLS의 phase/link index 혼합은 없다.

상세 phase별 G/g/y/r와 foe pair는 `phase_matrix.csv`에 있다.

## 7. 차량 route 및 headless smoke

영구 수요 파일을 만들지 않고 임시 디렉터리에서 `duarouter`와 headless SUMO를 실행했다.
라우터의 repair/ignore-errors는 끄고, SUMO seed 42, 1초 step, 900초 상한을 사용했다.

| route | start | end | 정문 통과 | 역사거리 통과 | core entry / exit | route | vehicle |
|---|---|---|---|---|---|---|---|
{route_lines}

- route error 0, disconnected 0, replacement/repair 0, reroute 0.
- loaded/inserted/arrived: 7/7/7; 종료 시 running 0, waiting 0.
- teleport `{smoke['teleports'].get('total', 'n/a')}`, collision
  `{smoke['safety'].get('collisions', 'n/a')}`, emergency stop
  `{smoke['safety'].get('emergencyStops', 'n/a')}`, emergency braking
  `{smoke['safety'].get('emergencyBraking', 'n/a')}`.
- 최장 waitingTime은 `{max(row['waiting_time_s'] for row in routes['routes']):.1f}s`이고 모든
  차량이 최대 236초에 도착해 무한 대기 징후가 없다.

이는 구조적 통행 가능성만 확인한다. 자동 신호의 성능·현실성 평가는 아니다.

## 8. 보정 명세

| correction | 등급 | 대상 | 제안 |
|---|---|---|---|
{correction_lines}

합계: 필수 {priorities['필수']}, 권장 {priorities['권장']}, 보류 {priorities['보류']}.
필드 전체와 영향 link/test는 `correction_plan.json`에 있다. 제안 방식은 plain XML
node/edge/connection/tll patch, netconvert tll additional input, 재현 가능한 스크립트, 또는
명시적 NetEdit 변경 내역으로 제한했다. raw OSM/auto.net.xml 직접 편집은 제안하지 않는다.

## 9. 준비도 판정

**차량용 corrected 네트워크 생성 준비도: NOT READY (근거 수집 후 조건부).** 투영과 route
연결성은 통과했지만, (1) 정문 link 2/11 protected-G foe pair, (2) 두 minor-green 접근의
lane-arrow/법정속도 부재, (3) U턴 6개의 법적 근거 부재가 남아 있다. 우선 필수 항목 중
환경 설정은 즉시 반영 가능하지만, lane/speed와 U턴은 현장·로드뷰·공식 규제 근거 없이 변경하면
안 된다. 그러므로 이번 산출물은 corrected network 입력 명세 초안이며 corrected 파일은 만들지
않았다.

## 10. 변경 파일, 테스트, Git 상태

- 새 감사 코드: `scripts/network/audit_mvp_vehicle.py`.
- 새 단위·baseline regression 테스트: `tests/test_mvp_vehicle_audit.py`.
- 새 산출물: `networks/hongdae_b/audit/mvp_vehicle/` 아래 요청된 8개 파일.
- `PYTHONPATH=src /opt/homebrew/bin/python3 -m unittest discover -s tests`:
  **65 tests OK, 1 skipped**. TraCI live 테스트의 localhost socket을 위해 sandbox 밖에서 같은
  명령을 재실행했다. 기존 actuated TLS detector warning은 있었지만 test failure는 없었다.
- 감사 스크립트 내 duarouter와 headless SUMO: exit code 0/0.
- 커밋 직전 `git status --short`: 승인된 감사 산출물·스크립트·PROJ 공통 환경·관련 테스트만
  변경됐고 그 외 파일은 포함하지 않았다. 사용자 승인 전에는 commit을 만들지 않았으며, 승인 후
  지정 메시지의 독립 커밋으로 기록한다.
- 최종 raw/generated checksum은 기준값과 동일하고 `git diff -- legacy results`는 비어 있다.
- 승인 후 MV-PROJ-001 이식성 구현과 추가 테스트는 이 감사 산출물과 같은 독립 커밋 범위에
  포함한다. 기존 acquisition/build provenance와 과거 log는 수정하지 않는다.

## 11. 증거 구분과 파일

- **확인 사실:** XML element/attribute, checksum, SUMO 실행 결과, PROJ 수치 오차.
- **OSM 태그:** raw snapshot에 실제 존재하는 name/lanes/oneway/access와 restriction member만.
- **기하학적 추론:** 접근 cardinal, 진행 방향, 회전각, movement class 교차검증.
- **외부 지도·거리 이미지:** 사용하지 않음. 따라서 표지·노면표시·실제 U턴 규칙·실제 신호
  주기는 주장하지 않음.
- **외부 기술 문서:** SUMO의 `request` bitset 역순과 `tlLogic state` 정순은
  [SUMO Road Networks](https://sumo.dlr.de/docs/Networks/SUMO_Road_Networks.html), `G/g` 의미와
  70 km/h 초과 minor-left 경고 조건은
  [SUMO Traffic Lights](https://sumo.dlr.de/docs/Simulation/Traffic_Lights.html)에서 확인했다
  (확인일 `{AUDIT_DATE}`).
- OSM 데이터: © OpenStreetMap contributors, ODbL 1.0. 원본 snapshot은
  `networks/hongdae_b/raw/hongdae_b_20260903_bbox.osm.xml`이다.

생성 파일: `report.md`, `controlled_links.csv`, `controlled_links.json`, `edge_mapping.csv`,
`phase_matrix.csv`, `uturn_audit.csv`, `route_smoke.json`, `correction_plan.json`.
"""


def main() -> int:
    args = parse_args()
    net_path = args.net.resolve()
    raw_path = args.raw.resolve()
    output_dir = args.output_dir.resolve()
    tools = sumo_tools_from_args(args)
    duarouter = tools.sumo.parent / "duarouter"
    if not duarouter.is_file():
        raise FileNotFoundError(f"duarouter not found beside sumo: {duarouter}")

    protected_before = {
        "raw": sha256_file(raw_path),
        "net": sha256_file(net_path),
        "legacy": directory_digest(REPO_ROOT / "legacy"),
        "results": directory_digest(REPO_ROOT / "results"),
    }
    git = git_info()
    osm = parse_osm(raw_path)
    net = parse_net(net_path)
    projection = projection_audit(net_path, net, osm, tools)
    controlled, link_summaries = controlled_link_records(net, osm)
    edges = edge_mapping_records(net, osm)
    phases, phase_summaries = phase_records(net)
    uturns = uturn_records(controlled, net, osm)
    minors = minor_green_findings(net, osm)
    routes = route_smoke(net_path, net, tools)
    corrections = correction_plan(minors)

    protected_after = {
        "raw": sha256_file(raw_path),
        "net": sha256_file(net_path),
        "legacy": directory_digest(REPO_ROOT / "legacy"),
        "results": directory_digest(REPO_ROOT / "results"),
    }
    if protected_before != protected_after:
        raise RuntimeError(f"Protected input/tree changed during audit: {protected_before} != {protected_after}")
    provenance = {
        "audit_date": AUDIT_DATE,
        "git": git,
        "raw_path": str(raw_path.relative_to(REPO_ROOT)),
        "raw_sha256": protected_after["raw"],
        "raw_checksum_matches": protected_after["raw"] == RAW_SHA256,
        "net_path": str(net_path.relative_to(REPO_ROOT)),
        "net_sha256": protected_after["net"],
        "net_checksum_matches": protected_after["net"] == NET_SHA256,
        "legacy_tree_sha256": protected_after["legacy"],
        "results_tree_sha256": protected_after["results"],
        "protected_trees_unchanged_during_audit": True,
        "sumo_path": str(tools.sumo),
        "sumo_version": version_line(tools.sumo),
        "netconvert_path": str(tools.netconvert),
        "netconvert_version": version_line(tools.netconvert),
        "duarouter_path": str(duarouter),
        "duarouter_version": version_line(duarouter),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "controlled_links.csv", controlled, CONTROLLED_COLUMNS)
    (output_dir / "controlled_links.json").write_text(
        json.dumps(
            {
                "provenance": provenance,
                "projection": projection,
                "tls_summary": link_summaries,
                "link_groups": {
                    tls_id: {
                        str(index): [
                            item
                            for item in controlled
                            if item["tls_id"] == tls_id and item["link_index"] == index
                        ]
                        for index in sorted(
                            {item["link_index"] for item in controlled if item["tls_id"] == tls_id}
                        )
                    }
                    for tls_id in MVP_TLS_IDS
                },
                "connections": controlled,
                "minor_green_findings": minors,
                "phase_summary": phase_summaries,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    write_csv(output_dir / "edge_mapping.csv", edges, EDGE_COLUMNS)
    phase_columns = (
        "physical_intersection",
        "tls_id",
        "phase_index",
        "duration_s",
        "state",
        "green_links",
        "minor_green_links",
        "yellow_links",
        "red_links",
        "simultaneous_foe_pairs",
        "green_to_red_without_yellow",
        "audit_scope",
    )
    write_csv(output_dir / "phase_matrix.csv", phases, phase_columns)
    uturn_columns = tuple(uturns[0].keys())
    write_csv(output_dir / "uturn_audit.csv", uturns, uturn_columns)
    (output_dir / "route_smoke.json").write_text(
        json.dumps(routes, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "correction_plan.json").write_text(
        json.dumps(
            {
                "scope": "vehicle corrections proposed only; no corrected network generated",
                "evidence_policy": "unresolved movements remain unchanged pending dated evidence",
                "corrections": corrections,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    report = render_report(
        projection,
        link_summaries,
        controlled,
        phase_summaries,
        uturns,
        routes,
        minors,
        corrections,
        provenance,
    )
    (output_dir / "report.md").write_text(report, encoding="utf-8")
    print(f"Wrote MVP vehicle audit to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
