# TrafficSnapshot domain model and SUMOAdapter

## Boundary and schema

`hongdae_traffic.domain` is a pure Python domain layer. It imports neither TraCI/SUMO nor
Stable-Baselines3, sumo-rl, YOLO, or ByteTrack. `SUMOAdapter` is the integration boundary:
it accepts a TraCI-like connection and returns only frozen domain dataclasses.

The current pre-release schema version is `0.1.0`. Deserialization rejects another version and
unknown or missing JSON keys instead of silently changing meaning.

```text
TrafficSnapshot
├── schema_version, timestamp_seconds, sequence_index, source
├── intersections[]
│   └── IntersectionSnapshot
│       ├── intersection_id
│       ├── signal_controllers[]: SignalControllerSnapshot
│       │   └── tls_id, active_phase, signal_state, phase_elapsed_seconds
│       ├── lanes[]: VehicleLaneSnapshot
│       ├── pedestrian_crossings[]: PedestrianCrossingSnapshot
│       └── confidence, missing_fields, unsupported_fields
└── diagnostics: TrafficDiagnostics
    └── departed, arrived, throughput, teleport, missing_fields, unsupported_fields
```

`diagnostics` is deliberately outside every `IntersectionSnapshot`. It is cumulative run-level
evaluation data, not control/RL state. A future observation builder may consume selected domain
fields, but `TrafficSnapshot` itself is not an RL observation vector.

## Units, ranges, and missing values

| Field | Definition |
|---|---|
| `timestamp_seconds` | time in seconds on the source's own timeline; metadata excluded by `comparable_domain_dict()` |
| `sequence_index` | source-neutral ordering key: SUMO step, video frame/window number, or real-time snapshot sequence |
| counts | non-negative integer; `null` means unavailable, never an inferred zero |
| `density` | `vehicle_count / (lane_length / 7.5 m)`, clipped to `[0, 1]` |
| `queue_ratio` | SUMO native halting count (default threshold 0.1 m/s) divided by the same capacity, clipped to `[0, 1]` |
| `max_wait_seconds` | maximum TraCI `vehicle.getWaitingTime` over vehicles currently on the lane |
| `mean_speed` | m/s; `null` when the lane contains no vehicles |
| `downstream_occupancy` | maximum reachable downstream lane's TraCI occupancy percent, converted to `[0, 1]` |
| confidence | `[0, 1]`; SUMO measurements use `1.0` |
| `phase_elapsed_seconds` | time spent in the active phase, in seconds |

An empty incoming lane has genuine counts, density, queue, and maximum wait of zero. Its mean
speed is undefined, so it is `null`; this is not a sensor failure and is not placed in
`missing_fields`.

Availability has three deliberately small representations:

- `null`: no value, including a value that is not applicable in the current state;
- `missing_fields`: the adapter normally provides the field, but this observation failed;
- `unsupported_fields`: this adapter does not implement the feature.

Pedestrian collection is not implemented in this stage: `pedestrian_crossings` is empty and
`unsupported_fields` contains `pedestrian_crossings`. `PedestrianCrossingSnapshot` fields remain
nullable so a later adapter can represent a known crossing with partially unavailable values.

SUMO confidence `1.0` means that the value was read directly from deterministic simulator state.
It does not mean 100% agreement with real traffic or 100% model accuracy. Vision confidence has
source-specific sensor/model semantics and must not be compared with SUMO confidence as a common
performance score.

## Intersection configuration

[`legacy_gate.toml`](../configs/intersections/legacy_gate.toml) contains four separate concerns:

- intersection identity: one intersection ID, its TLS IDs, and adjacent intersection IDs;
- physical lane mapping: six incoming lanes, approach IDs, and reachable downstream lanes;
- three legal green phases: native phase index and exact signal state;
- seventeen movements: controlled-link index, from/to lane, turn, and service phase IDs.

Several lanes occur in multiple movement records. No vehicle count, queue, wait, or density is
split among those movements. The downstream aggregation policy is explicit in `[measurement]`.
At adapter startup, configured TLS IDs, controlled-lane order, all controlled links, and legal
green phase states are checked against the running SUMO program.

The configuration uses movement model A: multiple `MovementConfiguration` records may share one
`(tls_id, link_index)`, with one record for each `(from_lane_id, to_lane_id)` connection. This keeps
movement semantics and service-phase mapping in one place and avoids adding a second signal-link
abstraction before it is needed. Runtime validation compares the complete configured connection
set with the complete `getControlledLinks(link_index)` set; subset matches fail.

## Usage

The reproducible headless example uses corrected-baseline route generation, native Actuated,
SUMO seed 101, and collects every step through step 40:

```bash
PYTHONPATH=src python3 scripts/capture_snapshot.py \
  --seed 101 --step 40 --output /tmp/hongdae_snapshot.json
```

Programmatic serialization is independent of SUMO:

```python
payload = snapshot.to_json(indent=2)
restored = TrafficSnapshot.from_json(payload)
assert restored == snapshot
```

For complete diagnostics, construct `SUMOAdapter` at simulation step zero and call `collect()`
once after every `simulationStep()`. Repeated collection in one step is idempotent. If collection
skips a step, cumulative counters become `null` and their names enter diagnostics
`missing_fields`; the adapter does not present a partial count as complete.

The captured live example at seed 101 / step 40 had phase 4,
`rrGGrrrrrrrGGrrrr`, 12 departed, 3 arrived/throughput, and 0 teleports. One southwest lane held
one halting vehicle with a 3-second maximum wait. The complete captured payload is
[`examples/live_sumo_snapshot_seed101_step40.json`](examples/live_sumo_snapshot_seed101_step40.json),
and its full JSON shape is exercised by the live round-trip test.

## Known limitations and two-intersection MVP

- This stage supports vehicle lanes only. One intersection may contain one or more TLS controllers.
- SUMO lane occupancy is an aggregate from the previous simulation step. The configured maximum
  across reachable downstream lanes is conservative and is not a per-movement estimate.
- SUMO `getWaitingTime` is the current accumulated waiting episode as defined by TraCI; it is not
  the trip-level waiting time later read from `tripinfo.xml`.
- Diagnostics completeness depends on continuous collection from step zero.
- Confidence is source confidence, not a calibrated uncertainty model; SUMO uses `1.0`.

For the two-intersection MVP, add a second intersection TOML with its TLS controllers, lanes, legal phases,
movements, and reciprocal adjacency; pass both configurations to `SUMOAdapter`; add a network
fixture and route set; then extend the existing runtime, round-trip, and deterministic replay tests
to assert two `IntersectionSnapshot` items. The domain schema and dataclasses do not need to change.
Coordination features, phase safety, pedestrians, RL training, and vision remain later layers.

## Version policy

Until `1.0.0`, a breaking schema change increments the minor version (`0.1` to `0.2`) and a
backward-compatible correction or optional addition increments the patch version. After `1.0.0`,
breaking changes increment major, backward-compatible additions increment minor, and fixes
increment patch. No compatibility shim is retained for unpublished pre-release field names.
