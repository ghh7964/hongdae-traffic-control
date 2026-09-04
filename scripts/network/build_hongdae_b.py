#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from .common import (
        NETWORK_ROOT,
        EXTRACTION_BBOX,
        REPO_ROOT,
        SumoTools,
        add_sumo_tool_arguments,
        bbox_text,
        command_text,
        copy_exclusive,
        ensure_paths_absent,
        first_output_line,
        git_state,
        sha256_file,
        structural_xml_sha256,
        sumo_subprocess_environment,
        sumo_tools_from_args,
        write_json_exclusive,
        write_text_exclusive,
    )
except ImportError:
    from common import (
        NETWORK_ROOT,
        EXTRACTION_BBOX,
        REPO_ROOT,
        SumoTools,
        add_sumo_tool_arguments,
        bbox_text,
        command_text,
        copy_exclusive,
        ensure_paths_absent,
        first_output_line,
        git_state,
        sha256_file,
        structural_xml_sha256,
        sumo_subprocess_environment,
        sumo_tools_from_args,
        write_json_exclusive,
        write_text_exclusive,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the uncorrected Hongdae B SUMO network")
    parser.add_argument("--input", type=Path, help="Immutable OSM XML input")
    parser.add_argument("--dry-run", action="store_true")
    add_sumo_tool_arguments(parser)
    return parser.parse_args()


def find_raw_input(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    candidates = sorted((NETWORK_ROOT / "raw").glob("hongdae_b_*_bbox.osm.xml"))
    if len(candidates) != 1:
        raise RuntimeError(f"Expected exactly one raw Hongdae B OSM file, found {len(candidates)}")
    return candidates[0].resolve()


def make_netconvert_command(
    tools: SumoTools, raw_path: Path, output: Path, plain_prefix: Path
) -> list[str]:
    return [
        str(tools.netconvert),
        "--osm-files",
        str(raw_path),
        "--type-files",
        f"{tools.vehicle_typemap},{tools.pedestrian_typemap}",
        "--osm.sidewalks",
        "--osm.crossings",
        "--osm.turn-lanes",
        "--osm.lane-access",
        "--walkingareas",
        "--output.street-names",
        "--output.original-names",
        "--write-license",
        "--keep-edges.in-geo-boundary",
        bbox_text(EXTRACTION_BBOX),
        "--plain-output-prefix",
        str(plain_prefix),
        "--output-file",
        str(output),
        "--verbose",
    ]


def run(command: list[str], tools: SumoTools) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=sumo_subprocess_environment(tools),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def main() -> int:
    args = parse_args()
    tools = sumo_tools_from_args(args)
    raw_path = find_raw_input(args.input)
    if not raw_path.exists():
        raise FileNotFoundError(raw_path)

    generated_dir = NETWORK_ROOT / "generated"
    audit_dir = NETWORK_ROOT / "audit"
    provenance_dir = NETWORK_ROOT / "provenance"
    output_path = generated_dir / "hongdae_b.auto.net.xml"
    command_path = provenance_dir / "netconvert.command.txt"
    metadata_path = provenance_dir / "build.json"
    log_path = audit_dir / "netconvert.log"
    reproducibility_log = audit_dir / "netconvert.reproducibility.log"
    reload_log = audit_dir / "network_reload.log"
    sumo_log = audit_dir / "sumo_load.log"

    canonical_command = make_netconvert_command(
        tools,
        raw_path,
        output_path,
        generated_dir / "hongdae_b.auto",
    )
    if args.dry_run:
        print(command_text(canonical_command))
        return 0

    ensure_paths_absent(
        [output_path, command_path, metadata_path, log_path, reproducibility_log, reload_log, sumo_log]
    )
    generated_dir.mkdir(parents=True, exist_ok=True)
    audit_dir.mkdir(parents=True, exist_ok=True)
    provenance_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="hongdae-build-") as first_tmp, tempfile.TemporaryDirectory(
        prefix="hongdae-rebuild-"
    ) as second_tmp:
        first_root = Path(first_tmp)
        second_root = Path(second_tmp)
        first_net = first_root / "hongdae_b.auto.net.xml"
        second_net = second_root / "hongdae_b.auto.net.xml"
        first_command = make_netconvert_command(
            tools, raw_path, first_net, first_root / "hongdae_b.auto"
        )
        second_command = make_netconvert_command(
            tools, raw_path, second_net, second_root / "hongdae_b.auto"
        )

        first_result = run(first_command, tools)
        if first_result.returncode != 0:
            raise RuntimeError(f"Initial netconvert failed:\n{first_result.stdout}")
        second_result = run(second_command, tools)
        if second_result.returncode != 0:
            raise RuntimeError(f"Reproducibility netconvert failed:\n{second_result.stdout}")

        first_structure = structural_xml_sha256(first_net)
        second_structure = structural_xml_sha256(second_net)
        if first_structure != second_structure:
            raise RuntimeError(
                "Repeated netconvert runs produced different structural XML hashes: "
                f"{first_structure} != {second_structure}"
            )

        reload_path = first_root / "reloaded.net.xml"
        reload_command = [
            str(tools.netconvert),
            "--sumo-net-file",
            str(first_net),
            "--output-file",
            str(reload_path),
        ]
        reload_result = run(reload_command, tools)
        if reload_result.returncode != 0:
            raise RuntimeError(f"netconvert could not reload generated network:\n{reload_result.stdout}")

        sumo_command = [
            str(tools.sumo),
            "--net-file",
            str(first_net),
            "--begin",
            "0",
            "--end",
            "0",
            "--no-step-log",
            "true",
        ]
        sumo_result = run(sumo_command, tools)
        if sumo_result.returncode != 0:
            raise RuntimeError(f"SUMO could not load generated network:\n{sumo_result.stdout}")

        generated_files = sorted(first_root.glob("hongdae_b.auto*.xml"))
        if first_net not in generated_files:
            raise RuntimeError("Expected auto network was not among generated artifacts")
        for source in generated_files:
            copy_exclusive(source, generated_dir / source.name)

        write_text_exclusive(command_path, command_text(canonical_command) + "\n")
        write_text_exclusive(log_path, first_result.stdout)
        write_text_exclusive(reproducibility_log, second_result.stdout)
        write_text_exclusive(reload_log, command_text(reload_command) + "\n" + reload_result.stdout)
        write_text_exclusive(sumo_log, command_text(sumo_command) + "\n" + sumo_result.stdout)

        metadata = {
            "schema_version": 1,
            "built_at": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
            "timezone": "Asia/Seoul",
            "raw_file": str(raw_path.relative_to(REPO_ROOT)),
            "raw_sha256": sha256_file(raw_path),
            "output_file": str(output_path.relative_to(REPO_ROOT)),
            "output_sha256": sha256_file(output_path),
            "structural_sha256": structural_xml_sha256(output_path),
            "repeated_structural_sha256": second_structure,
            "structure_reproducible": first_structure == second_structure,
            "generated_files": {
                path.name: sha256_file(path) for path in sorted(generated_dir.glob("hongdae_b.auto*.xml"))
            },
            "vehicle_typemap": {
                "path": str(tools.vehicle_typemap),
                "sha256": sha256_file(tools.vehicle_typemap),
            },
            "pedestrian_typemap": {
                "path": str(tools.pedestrian_typemap),
                "sha256": sha256_file(tools.pedestrian_typemap),
            },
            "canonical_command": command_text(canonical_command),
            "execution_environment": {
                "SUMO_HOME": str(tools.sumo_home),
                "PROJ_DATA": str(tools.proj_data) if tools.proj_data else None,
            },
            "proj_data_source": tools.proj_data_source,
            "sumo_version": first_output_line([str(tools.sumo), "--version"]),
            "netconvert_version": first_output_line([str(tools.netconvert), "--version"]),
            "resolved_tools": {
                "sumo": str(tools.sumo),
                "netconvert": str(tools.netconvert),
                "osm_get": str(tools.osm_get),
                "netcheck": str(tools.netcheck),
                "vehicle_typemap": str(tools.vehicle_typemap),
                "pedestrian_typemap": str(tools.pedestrian_typemap),
                "proj_data": str(tools.proj_data) if tools.proj_data else None,
            },
            "actual_commands": [
                command_text(first_command),
                command_text(second_command),
                command_text(reload_command),
                command_text(sumo_command),
            ],
            "validation": {
                "netconvert_reload": "passed",
                "sumo_load_without_demand": "passed",
            },
            "deferred_options": [
                "geometry.remove",
                "junctions.join",
                "tls.join",
                "tls.guess",
                "tls.guess-signals",
                "sidewalks.guess",
                "crossings.guess",
                "tls.default-type",
            ],
            "git": git_state(),
        }
        write_json_exclusive(metadata_path, metadata)

    print(output_path)
    print(metadata_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
