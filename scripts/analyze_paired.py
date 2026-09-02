#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hongdae_baseline.statistics import (  # noqa: E402
    controller_summary,
    paired_statistics,
    read_results,
    render_markdown_report,
    validate_paired_results,
    write_csv,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and summarize a complete paired baseline run")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--seed-file", type=Path, default=ROOT / "configs" / "vehicle_only_20_seeds.json")
    parser.add_argument("--report", type=Path, default=ROOT / "docs" / "vehicle_only_20_seed_report.md")
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    manifest_path = run_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "complete":
        raise ValueError(f"Run manifest is not complete: {manifest.get('status')}")
    seeds = json.loads(args.seed_file.read_text(encoding="utf-8"))["seeds"]
    data = validate_paired_results(read_results(run_dir / "results.csv"), seeds)
    controller_rows = controller_summary(data)
    differences, paired_rows = paired_statistics(data)
    write_csv(run_dir / "controller_summary.csv", controller_rows)
    write_csv(run_dir / "paired_differences.csv", differences)
    write_csv(run_dir / "paired_summary.csv", paired_rows)
    report = render_markdown_report(data, controller_rows, paired_rows, manifest_path)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    print(f"Validated {len(data.seeds)} seeds x 4 controllers")
    print(args.report.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
