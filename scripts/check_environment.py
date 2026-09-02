#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hongdae_baseline.assets import AssetManifest, find_sumo_binary, runtime_environment  # noqa: E402
from hongdae_baseline.config import load_config  # noqa: E402


def main() -> int:
    argument_parser = argparse.ArgumentParser(description="Check local SUMO baseline prerequisites")
    argument_parser.add_argument("--output", type=Path)
    args = argument_parser.parse_args()
    config = load_config("corrected_baseline", ROOT)
    assets = AssetManifest(config.asset_manifest, ROOT)
    report: dict[str, object] = {
        "runtime": runtime_environment(),
        "asset_hashes": assets.verify_all(),
        "xml": {},
        "headless_smoke": None,
    }
    for name, path in (("network", config.network), ("representative_route", config.route_template)):
        root = ET.parse(path).getroot()
        report["xml"][name] = {"path": str(path), "root_tag": root.tag, "valid": True}

    sumo = find_sumo_binary("sumo")
    status = 0
    if sumo is None:
        report["headless_smoke"] = {
            "status": "blocked",
            "reason": "sumo executable not installed or not discoverable",
        }
        status = 2
    else:
        with tempfile.TemporaryDirectory(prefix="hongdae_sumo_smoke_") as directory:
            tripinfo = Path(directory) / "tripinfo.xml"
            command = [
                str(sumo),
                "--net-file",
                str(config.network),
                "--route-files",
                str(config.route_template),
                "--begin",
                "0",
                "--end",
                "10",
                "--seed",
                "1",
                "--tripinfo-output",
                str(tripinfo),
                "--no-step-log",
                "true",
                "--duration-log.disable",
                "true",
            ]
            completed = subprocess.run(command, check=False, capture_output=True, text=True)
            report["headless_smoke"] = {
                "status": "passed" if completed.returncode == 0 else "failed",
                "command": command,
                "exit_code": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            }
            status = 0 if completed.returncode == 0 else 1
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return status


if __name__ == "__main__":
    raise SystemExit(main())

