#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from .common import (
        EXTRACTION_BBOX,
        NETWORK_ROOT,
        OVERPASS_ENDPOINT,
        REPO_ROOT,
        SumoTools,
        add_sumo_tool_arguments,
        bbox_text,
        command_text,
        copy_exclusive,
        ensure_paths_absent,
        first_output_line,
        git_state,
        osm_ids_present,
        sumo_subprocess_environment,
        sumo_tools_from_args,
        sha256_file,
        write_json_exclusive,
        write_text_exclusive,
    )
except ImportError:
    from common import (
        EXTRACTION_BBOX,
        NETWORK_ROOT,
        OVERPASS_ENDPOINT,
        REPO_ROOT,
        SumoTools,
        add_sumo_tool_arguments,
        bbox_text,
        command_text,
        copy_exclusive,
        ensure_paths_absent,
        first_output_line,
        git_state,
        osm_ids_present,
        sumo_subprocess_environment,
        sumo_tools_from_args,
        sha256_file,
        write_json_exclusive,
        write_text_exclusive,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch the immutable Hongdae B OSM snapshot")
    parser.add_argument("--dry-run", action="store_true", help="Print the planned command only")
    parser.add_argument("--endpoint", default=OVERPASS_ENDPOINT)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument(
        "--expected-head",
        help="Optionally require the current Git HEAD (full hash or >=7-char prefix)",
    )
    add_sumo_tool_arguments(parser)
    return parser.parse_args()


def make_command(
    tools: SumoTools,
    output_dir: Path,
    prefix: str,
    query_path: Path,
    config_path: Path,
    endpoint: str,
    retries: int,
) -> list[str]:
    return [
        sys.executable,
        str(tools.osm_get),
        "--bbox",
        bbox_text(EXTRACTION_BBOX),
        "--prefix",
        prefix,
        "--output-dir",
        str(output_dir),
        "--url",
        endpoint,
        "--road-types",
        json.dumps(
            {"highway": ["."], "railway": ["subway_entrance", "station"]},
            separators=(",", ":"),
        ),
        "--query-output",
        str(query_path),
        "--config-output",
        str(config_path),
        "--retries",
        str(retries),
        "--verbose",
    ]


def mirrored_config(command: list[str]) -> str:
    """Preserve the effective osmGet configuration as standalone XML.

    SUMO 1.27.1 accepts --config-output but does not materialize that file.  Its
    own generated OSM header still embeds this configuration.  Keeping this
    explicit mirror makes the acquisition reproducible without changing the
    downloaded response body.
    """
    pairs = []
    index = 2
    while index < len(command):
        option = command[index]
        if option == "--verbose":
            pairs.append(("verbose", "true"))
            index += 1
            continue
        if option.startswith("--") and index + 1 < len(command):
            pairs.append((option[2:], command[index + 1]))
            index += 2
            continue
        index += 1
    values = "\n".join(
        f'    <{key} value="{html.escape(value, quote=True)}"/>' for key, value in pairs
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!-- Wrapper-preserved effective osmGet configuration. -->\n'
        '<configuration>\n'
        f'{values}\n'
        '</configuration>\n'
    )


def execution_environment(tools: SumoTools) -> dict[str, str]:
    return sumo_subprocess_environment(tools, include_pythonpath=True)


def command_with_environment(tools: SumoTools, command: list[str]) -> list[str]:
    prefix = [
        "env",
        f"SUMO_HOME={tools.sumo_home}",
    ]
    if tools.proj_data is not None:
        prefix.append(f"PROJ_DATA={tools.proj_data}")
    return [*prefix, f"PYTHONPATH={tools.sumo_home / 'tools'}", *command]


def main() -> int:
    args = parse_args()
    tools = sumo_tools_from_args(args)
    seoul = ZoneInfo("Asia/Seoul")
    started = datetime.now(seoul)
    date_token = started.strftime("%Y%m%d")
    prefix = f"hongdae_b_{date_token}"

    raw_dir = NETWORK_ROOT / "raw"
    provenance_dir = NETWORK_ROOT / "provenance"
    raw_path = raw_dir / f"{prefix}_bbox.osm.xml"
    query_path = provenance_dir / "osmget.query.xml"
    config_path = provenance_dir / "osmget.config.xml"
    command_path = provenance_dir / "fetch.command.txt"
    log_path = provenance_dir / "osmget.log"
    metadata_path = provenance_dir / "acquisition.json"

    display_command = make_command(
        tools, raw_dir, prefix, query_path, config_path, args.endpoint, args.retries
    )
    if args.dry_run:
        print(command_text(command_with_environment(tools, display_command)))
        return 0

    state_before = git_state(args.expected_head)

    ensure_paths_absent(
        [raw_path, query_path, config_path, command_path, log_path, metadata_path]
    )
    for directory in (
        raw_dir,
        NETWORK_ROOT / "generated",
        NETWORK_ROOT / "corrected",
        NETWORK_ROOT / "audit",
        provenance_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=".fetch-", dir=NETWORK_ROOT) as temporary:
        temporary_root = Path(temporary)
        temporary_raw = temporary_root / "raw"
        temporary_provenance = temporary_root / "provenance"
        temporary_raw.mkdir()
        temporary_provenance.mkdir()
        temporary_query = temporary_provenance / "osmget.query.xml"
        temporary_config = temporary_provenance / "osmget.config.xml"
        actual_command = make_command(
            tools,
            temporary_raw,
            prefix,
            temporary_query,
            temporary_config,
            args.endpoint,
            args.retries,
        )
        # osmGet.py 1.27.1 accepts this option but does not create the file.
        # Materialize the exact effective values before execution.  The raw
        # response remains byte-for-byte untouched after osmGet writes it.
        temporary_config.write_text(mirrored_config(actual_command), encoding="utf-8")

        import subprocess

        completed = subprocess.run(
            actual_command,
            cwd=REPO_ROOT,
            env=execution_environment(tools),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"osmGet.py failed with exit code {completed.returncode}:\n{completed.stdout}"
            )

        temporary_osm = temporary_raw / f"{prefix}_bbox.osm.xml"
        if not temporary_osm.exists():
            raise RuntimeError(f"osmGet.py did not create {temporary_osm}")
        if ET.parse(temporary_osm).getroot().tag != "osm":
            raise RuntimeError("Downloaded file is not an OSM XML document")

        target_presence = osm_ids_present(temporary_osm)
        missing_targets = [name for name, present in target_presence.items() if not present]
        if missing_targets:
            raise RuntimeError(
                "Downloaded OSM snapshot is missing required target objects: "
                + ", ".join(missing_targets)
            )

        root = ET.parse(temporary_osm).getroot()
        meta = root.find("meta")
        osm_base = meta.attrib.get("osm_base") if meta is not None else None
        retry_count = len(re.findall(r"retrying", completed.stdout, flags=re.IGNORECASE))
        finished = datetime.now(seoul)

        metadata = {
            "schema_version": 1,
            "acquired_at": finished.isoformat(),
            "started_at": started.isoformat(),
            "timezone": "Asia/Seoul",
            "bbox_west_south_east_north": list(EXTRACTION_BBOX),
            "overpass_endpoint": args.endpoint,
            "requested_retries": args.retries,
            "observed_retry_count": retry_count,
            "osm_base": osm_base,
            "raw_file": str(raw_path.relative_to(REPO_ROOT)),
            "raw_size_bytes": temporary_osm.stat().st_size,
            "raw_sha256": sha256_file(temporary_osm),
            "raw_permissions": "0444",
            "target_osm_objects_present": target_presence,
            "query_file": str(query_path.relative_to(REPO_ROOT)),
            "config_file": str(config_path.relative_to(REPO_ROOT)),
            "command_file": str(command_path.relative_to(REPO_ROOT)),
            "log_file": str(log_path.relative_to(REPO_ROOT)),
            "display_command": command_text(command_with_environment(tools, display_command)),
            "actual_command": command_text(command_with_environment(tools, actual_command)),
            "sumo_version": first_output_line([str(tools.sumo), "--version"]),
            "netconvert_version": first_output_line([str(tools.netconvert), "--version"]),
            "sumolib_version": first_output_line(
                command_with_environment(
                    tools,
                    [sys.executable, "-c", "import sumolib; print(sumolib.version.gitDescribe())"]
                )
            ),
            "execution_environment": {
                "SUMO_HOME": str(tools.sumo_home),
                "PROJ_DATA": str(tools.proj_data) if tools.proj_data else None,
                "PYTHONPATH": str(tools.sumo_home / "tools"),
            },
            "proj_data_source": tools.proj_data_source,
            "resolved_tools": {
                "sumo": str(tools.sumo),
                "netconvert": str(tools.netconvert),
                "osm_get": str(tools.osm_get),
                "netcheck": str(tools.netcheck),
                "vehicle_typemap": str(tools.vehicle_typemap),
                "pedestrian_typemap": str(tools.pedestrian_typemap),
                "proj_data": str(tools.proj_data) if tools.proj_data else None,
            },
            "osm_get_path": str(tools.osm_get),
            "osm_get_sha256": sha256_file(tools.osm_get),
            "config_output_note": (
                "osmGet.py 1.27.1 accepts --config-output but does not create it; "
                "the wrapper preserved a standalone XML mirror of the effective options"
            ),
            "git": state_before,
            "attribution": "© OpenStreetMap contributors; data available under ODbL 1.0",
        }
        temporary_command = temporary_provenance / "fetch.command.txt"
        temporary_log = temporary_provenance / "osmget.log"
        temporary_metadata = temporary_provenance / "acquisition.json"
        temporary_command.write_text(
            command_text(command_with_environment(tools, display_command)) + "\n",
            encoding="utf-8",
        )
        temporary_log.write_text(completed.stdout, encoding="utf-8")
        temporary_metadata.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        # Publish only after download, XML validation, target validation, and
        # provenance construction have all succeeded.
        copy_exclusive(temporary_osm, raw_path)
        copy_exclusive(temporary_query, query_path)
        copy_exclusive(temporary_config, config_path)
        copy_exclusive(temporary_command, command_path)
        copy_exclusive(temporary_log, log_path)
        copy_exclusive(temporary_metadata, metadata_path)
        os.chmod(raw_path, 0o444)

    print(raw_path)
    print(metadata_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
