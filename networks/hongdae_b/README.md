# Hongdae B OSM/SUMO network

This directory is separate from the frozen single-intersection assets under `legacy/`.

## Fixed spatial scopes

- OSM extraction bbox (west, south, east, north): `126.9168,37.5510,126.9296,37.5605`
- Evaluation candidate bbox: `126.9188,37.5510,126.9283,37.5590`
- Initial control targets: OSM nodes `2959081059` and `3034197250`

The fetch query selects all OSM `highway` objects and only the `railway` values
`subway_entrance` and `station` in the bbox (plus the public-transport objects
included by SUMO's `osmGet.py`). Excluding long `railway=rail` ways prevents a
nominal bbox download from expanding along rail lines while retaining roads,
pedestrian ways, Exit 9, and the airport-railroad station reference point. Ways
crossing the bbox may still carry a node just outside the box; that is normal
OSM topology, not a wider query bbox.

`raw/` contains an immutable dated OpenStreetMap snapshot. Fetching refuses to overwrite an
existing snapshot or provenance file and makes the downloaded XML read-only. `generated/` contains
only reproducible automatic netconvert output. `corrected/` is reserved for a later reviewed stage;
no corrected network is created by the current scripts. `audit/` contains machine-readable and
human-readable inspection results.

## Attribution and license

Map data is © OpenStreetMap contributors and is available under the Open Database License (ODbL)
1.0. See <https://www.openstreetmap.org/copyright> and
<https://opendatacommons.org/licenses/odbl/1-0/>. The acquisition metadata records the exact bbox,
endpoint, timestamp, command, tool versions, file size, and SHA-256 checksum.

## Planned commands

Run from the repository root after reviewing the scripts:

```bash
python3 scripts/network/fetch_hongdae_b.py
python3 scripts/network/build_hongdae_b.py
python3 scripts/network/audit_hongdae_b.py
```

The automatic build deliberately does not use geometry removal, junction/TLS joining, TLS guessing,
sidewalk guessing, crossing guessing, or an invented actuated signal type.

## Portable SUMO tool discovery

The network scripts resolve each required tool in this order: an explicit CLI
path, `SUMO_HOME`, `PATH` (including a `share/sumo` tree inferred from the
binary location), and finally the known local macOS framework path. This also
supports the usual Linux/Colab layout where binaries are on `PATH` and
`SUMO_HOME` points to `/usr/share/sumo`.

Use `--sumo-home` or the individual `--sumo`, `--netconvert`, `--osm-get`,
`--netcheck`, `--vehicle-typemap`, and `--pedestrian-typemap` options when an
installation is non-standard. Fetching records the current Git state without
requiring a particular commit; pass optional `--expected-head HASH` only when
a caller wants that additional guard.
