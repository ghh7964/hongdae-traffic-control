#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hongdae_baseline.assets import AssetManifest  # noqa: E402


def main() -> int:
    manifest = AssetManifest(ROOT / "configs" / "assets_manifest.json", ROOT)
    for name, checksum in manifest.verify_all().items():
        print(f"OK {name} {checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
