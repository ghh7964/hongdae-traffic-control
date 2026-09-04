# Hongdae B 초기 자동 네트워크 감사 보고서

## 1. 범위와 provenance

- OSM query bbox (W,S,E,N): `126.9168,37.551,126.9296,37.5605`
- 평가 후보 bbox: `126.9188,37.551,126.9283,37.559` (자동 평가 edge 선택 규칙이 아님)
- 취득 시각: `2026-09-03T14:35:14.736285+09:00` (`Asia/Seoul`), OSM base `2026-09-03T05:33:28Z`
- Overpass endpoint: `https://overpass-api.de/api/interpreter`; 관측된 재시도 0회
- 원본: `networks/hongdae_b/raw/hongdae_b_20260903_bbox.osm.xml`; 393095 bytes; SHA-256 `16c432a9591b4ab53c471633dd31967239031f49b20ceae9ff7560baf1a8fc61`
- 자동본: `networks/hongdae_b/generated/hongdae_b.auto.net.xml`; SHA-256 `c17729eb755e88e858ea6b5ad13332dd0bf3ecd17cd7673186037971555ec8f1`
- 구조 해시: `470dfeb975cdae93a75ad76b78e0a06c3f5d08de6302728ea25627042bce77c3`; 독립 2회 변환 동일 여부 `True`
- 도구: `Eclipse SUMO sumo 1.27.1`, `Eclipse SUMO netconvert 1.27.1`, osmGet SHA-256 `00e4034ebf5674221e676ee110a82cbb92cdfb599fb8ec7d8f578647900d38c4`
- osmGet가 불러온 번들 sumolib 경로는 1.27.1 설치 트리지만 Python distribution 메타데이터는 `v1.26.0`으로 보고되어 원본 헤더에도 이 값이 표시된다. 이는 provenance상 버전 표기 불일치로 보존한다.
- OSM 데이터는 © OpenStreetMap contributors, ODbL 1.0 조건을 따른다.
- 이 보고서는 자동 변환본만 감사한다. `corrected` 네트워크, 교차로 TOML, 수요, 실제 신호 주기는 생성하지 않았다.

원본 OSM 통계: node 2307, way 580, relation 17. Query는 고정 bbox이지만 완전한 OSM way topology 보존 때문에 bbox 밖 종속 node 550개가 원본에 포함된다. netconvert에서는 `--keep-edges.in-geo-boundary`로 확정 bbox를 적용했다.

## 2. 실제 실행 명령과 변환 정책

취득 명령:

```text
env SUMO_HOME=/Library/Frameworks/EclipseSUMO.framework/Versions/1.27.1/EclipseSUMO/share/sumo PYTHONPATH=/Library/Frameworks/EclipseSUMO.framework/Versions/1.27.1/EclipseSUMO/share/sumo/tools /opt/homebrew/opt/python@3.13/bin/python3.13 /Library/Frameworks/EclipseSUMO.framework/Versions/1.27.1/EclipseSUMO/share/sumo/tools/osmGet.py --bbox 126.9168,37.5510,126.9296,37.5605 --prefix hongdae_b_20260903 --output-dir /Users/ghh7964/Documents/VSCode/hongdae-traffic-control/networks/hongdae_b/raw --url https://overpass-api.de/api/interpreter --road-types '{"highway":["."],"railway":["subway_entrance","station"]}' --query-output /Users/ghh7964/Documents/VSCode/hongdae-traffic-control/networks/hongdae_b/provenance/osmget.query.xml --config-output /Users/ghh7964/Documents/VSCode/hongdae-traffic-control/networks/hongdae_b/provenance/osmget.config.xml --retries 5 --verbose
```

자동 변환 명령:

```text
/Library/Frameworks/EclipseSUMO.framework/Versions/1.27.1/EclipseSUMO/bin/netconvert --osm-files /Users/ghh7964/Documents/VSCode/hongdae-traffic-control/networks/hongdae_b/raw/hongdae_b_20260903_bbox.osm.xml --type-files /Library/Frameworks/EclipseSUMO.framework/Versions/1.27.1/EclipseSUMO/share/sumo/data/typemap/osmNetconvert.typ.xml,/Library/Frameworks/EclipseSUMO.framework/Versions/1.27.1/EclipseSUMO/share/sumo/data/typemap/osmNetconvertPedestrians.typ.xml --osm.sidewalks --osm.crossings --osm.turn-lanes --osm.lane-access --walkingareas --output.street-names --output.original-names --write-license --keep-edges.in-geo-boundary 126.9168,37.5510,126.9296,37.5605 --plain-output-prefix /Users/ghh7964/Documents/VSCode/hongdae-traffic-control/networks/hongdae_b/generated/hongdae_b.auto --output-file /Users/ghh7964/Documents/VSCode/hongdae-traffic-control/networks/hongdae_b/generated/hongdae_b.auto.net.xml --verbose
```

차량·보행 typemap, OSM sidewalk/crossing/turn lane/lane access, walking area, 도로명, 원 OSM ID, license metadata, plain XML 출력을 사용했다. `geometry.remove`, `junctions.join`, `tls.join`, `tls.guess`, `tls.guess-signals`, `sidewalks.guess`, `crossings.guess`, 임의 actuated 지정은 사용하지 않았다.

## 3. 기본 통계

| Metric | Value |
|---|---:|
| Junctions | 1646 |
| Edges (total / normal) | 7019 / 2092 |
| Lanes | 7665 |
| Traffic-light junctions | 20 |
| tlLogic elements | 20 |
| Controlled links | 114 |
| Pedestrian-allowed lanes | 2624 |
| Crossings | 2 |
| Walking areas | 518 |
| Dead-end junction candidates | 69 |
| Fringe junctions | 1 |
| Junction-incidence weak components | 9 |
| Passenger junction-incidence weak components | 1 |
| netcheck exit code | 0 |
| netcheck connection components | 422 |
| netcheck largest-component coverage | 79.83% |

Junction types: `{"dead_end": 237, "internal": 621, "priority": 699, "right_before_left": 69, "traffic_light": 20}`

Edge functions: `{"crossing": 2, "internal": 4407, "normal": 2092, "walkingarea": 518}`

Passenger component sizes: `[592]`

## 4. 핵심 OSM ↔ SUMO 대응

| 실제 위치 | OSM 객체·좌표 | SUMO junction | 인접 또는 최근접 edge | tlLogic·제어 link | 자동 신호 | 보행 접근 | 확인 상태 | 보정/검토 |
|---|---|---|---|---|---|---|---|---|
| 홍익대학교 정문 앞 교차로 | `node/2959081059`<br>37.5528519, 126.9242676 | `2959081059` | `-168874251#0`<br>`-218976037#0`<br>`-299767124#4`<br>`-333681731#5`<br>`168874251#0`<br>`218976037#0`<br>`299767124#4`<br>`333681731#5` | `2959081059` (6 phases)<br>17 links {"l": 4, "r": 4, "s": 5, "t": 4} | 예 | direct-generated | exact-junction | P0-controlled-links-and-pedestrian-review |
| 홍대입구역사거리 | `node/3034197250`<br>37.5551295, 126.9215965 | `3034197250` | `-218976035#0`<br>`-254749392`<br>`218976035#0`<br>`254749392`<br>`333681721#0`<br>`333681730#7` | `3034197250` (6 phases)<br>19 links {"l": 4, "r": 3, "s": 10, "t": 2} | 예 | nearby-11.2m-connectivity-unverified | exact-junction | P0-controlled-links-and-pedestrian-review |
| 2호선 홍대입구역 9번 출구 | `node/3404932011`<br>37.5560397, 126.9229218 | — | `596037749#8` (~4.9 m)<br>`596037749#9` (~7.9 m)<br>`613369914#3` (~36.8 m) | —<br>0 links {} | 아니오/미확인 | nearby-4.9m-connectivity-unverified | raw-object-only-no-sumo-mapping | P1-pedestrian-connectivity-review |
| 공항철도·경의중앙선 역사 방향 | `node/5919544685`<br>37.5573487, 126.9269080 | — | `471699326#3` (~6.9 m)<br>`471699326#2` (~7.3 m)<br>`-614336024#1` (~13.2 m) | —<br>0 links {} | 아니오/미확인 | nearby-6.9m-connectivity-unverified | raw-object-only-no-sumo-mapping | P1-pedestrian-connectivity-review |
| 어울마당로/레드로드 북·중부 | `way/919071199`<br>way | — | `919071199` | —<br>0 links {} | 아니오/미확인 | direct-generated | related-osm-ways-mapped-no-object-junction | P1-access-and-pedestrian-review |

상세 lane ID와 최근접 보행 edge의 OSM way 및 거리는 `osm_sumo_mapping.json`과 CSV에 보존했다. 최근접 표시는 공간적 근접성만 뜻하며 연결 가능성을 보장하지 않는다.

## 5. 경고와 구조적 문제

netconvert 경고 총 45건, Error 표식 0건:

- 5× `Ignoring restriction relation '<id>' with unknown from-way.`
- 5× `Ignoring restriction relation '<id>' with unknown to-way.`
- 5× `Minor green from edge '<id>' to edge '<id>' exceeds <n>.44m/s. Maybe a left-turn lane is missing.`
- 5× `Speed of straight connection '<id>' reduced by <n> due to turning radius of <n> (length=<n>, angle=<n>).`
- 4× `Found angle of <n> degrees at edge '<id>', segment <n>.`
- 3× `Removing pt stop '<id>' on non existing edge '<id>'.`
- 3× `Found sharp turn with radius <n> at the start of edge '<id>'.`
- 2× `Could not find corresponding edge or compatible lane for free-floating pt stop '<id>' (홍대입구). Thus, it will be removed!`
- 2× `The traffic light '<id>' does not control any links; it will not be build.`
- 2× `Could not build program '<id>' for traffic light '<id>'`
- 1× `Could not assign stop '<id>' to pt line '<id>' (closest edge '<id>', distance <n>). Ignoring!`
- 1× `Removed invalid stop '<id>' from line '<id>'.`
- 1× `Cannot revise pt stop localization for pt line '<id>', which has no route edges. Ignoring!`
- 1× `Found sharp turn with radius <n> at the end of edge '<id>'.`
- 1× `Stop '<id>' named '<id>' from line '<id>' on edge '<id>' is not part of the route.`
- 1× `Removed <n> pt stops because they could not be assigned to the network`
- 1× `<n> total messages of type: Ignoring restriction relation '<id>' with unknown from-way.`
- 1× `<n> total messages of type: Minor green from edge '<id>' to edge '<id>' exceeds %m/s. Maybe a left-turn lane is missing.`
- 1× `<n> total messages of type: Speed of % connection '<id>' reduced by % due to turning radius of % (length=%, angle=%).`

기타 stderr 진단:

- 2× `pj_obj_create: Cannot find proj.db`

MVP 인접 edge 관련 경고:

- `Warning: Minor green from edge '254749392' to edge '515592172#0' exceeds 19.44m/s. Maybe a left-turn lane is missing.`
- `Warning: Minor green from edge '299767124#4' to edge '218976037#0' exceeds 19.44m/s. Maybe a left-turn lane is missing.`

## 6. 수동 보정·확인의 우선순위

### P0 — MVP 사용 전 필수

- 두 MVP node는 정확히 같은 ID의 traffic-light junction/tlLogic으로 생성됐다: 구조 식별 결과 `True`.
- 홍익대 정문 TLS는 17개, 홍대입구역사거리 TLS는 19개 controlled link를 가진다. 회전 방향과 U턴(`dir=t`)을 영상·현장·도로 규제 자료와 대조해야 한다.
- 홍익대 정문 인접 `299767124#4 → 218976037#0`에서 left-turn lane 누락 가능성을 알리는 minor-green 경고가 발생했다. 이 연결은 정문 MVP의 제어 link이므로 우선 검증 대상이다.
- 자동 생성된 `type=static` phase는 OSM 신호 존재로 만든 SUMO 기본 프로그램일 뿐 실제 신호 주기가 아니다. 제어 코드나 baseline의 실측 주기로 간주하면 안 된다.
- 전체 네트워크에 crossing이 2개뿐이다. 정문에는 walkingarea가 일부 생성됐지만 역사거리에는 교차로 소속 crossing/walkingarea가 확인되지 않아 보행 포함 MVP에는 바로 사용할 수 없다.

### P1 — 보행·접근·투영

- 9번 출구와 공항철도 역사 node는 SUMO junction으로 변환되지 않는 POI 성격이다. JSON에 기록된 최근접 보행 lane에서 실제 연결성·출발 위치를 수동 확인해야 하며 원본 OSM은 수정하지 않는다.
- OSM way 919071199는 `highway=pedestrian`과 `motor_vehicle=yes`를 함께 갖지만 자동본 edge `['919071199']`는 pedestrian 허용 lane으로 변환됐고, netcheck에서 단독 connection component로 분리됐다. Red Road 보행 흐름에는 현재 사용할 수 없으며 연결 복원과 제한적 차량 통행 정책을 함께 확인해야 한다.
- 전체 normal edge의 netcheck 결과는 422개 component, 최대 component coverage 79.83%로 **미연결**이다. junction incidence 기반 WCC와 달리 실제 connection을 따르는 검사이므로 보행 연결 감사에서 이 결과를 우선한다.
- `pj_obj_create: Cannot find proj.db`가 기록됐으나 UTM network 생성·재읽기는 성공했다. 다음 단계에서 geo 좌표 overlay를 시각 검증하기 전까지 투영 정확성을 확정하지 않는다.
- 자동 생성 실패한 비-MVP traffic signal `['11841458566', '436869362']`와 제거된 PT stop이 있다. 후속 제어 범위에 넣기 전에 원 OSM 위치와 용도를 확인한다.

### P2 — 차로·경계·연결성

- 차로 수, 일방통행, turn lane, 비정상 U턴, internal lane 형상을 항공사진·로드뷰·현장 자료와 대조한다.
- unknown from/to way인 restriction relation, 급회전·회전반경 감속 경고를 실제 허용 회전과 대조한다.
- dead-end 후보 69개와 fringe 표식 1개를 route 생성 전에 확인한다. 승용차 그래프는 weak component 1개(크기 [592])로 연결돼 있다.
- 남쪽 경계 및 모든 buffer 진입·이탈 edge는 최종 평가 목록에서 제외하고, 경계 edge에서 route 생성·종료가 가능한지는 수요 생성 전 별도 검증한다.

## 7. 평가 core 제안(미확정)

- seed junction: `['2959081059', '3034197250']`
- 두 교차로 간 양방향 최단 passenger 경로 edge: `['-218976035#0', '-218976035#1', '-218976035#2', '-299767124#0', '-299767124#1', '-299767124#2', '-299767124#3', '-299767124#4', '-299959899#0', '-299959899#1', '218976035#0', '218976035#1', '218976035#2', '299767124#0', '299767124#1', '299767124#2', '299767124#3', '299767124#4', '299959899#0', '299959899#1']`
- 후보 core edge 32개, core 진입 edge 6개, 이탈 edge 6개

최종 목록은 의도적으로 확정하지 않았다. 승인된 core edge에 실제 진입한 차량에만 `ever_entered_core`, `first_core_entry_time`, `last_core_exit_time`을 기록하고, core에 진입하지 않은 경계 통과 차량은 core 지체 지표에서 분리한다.

## 8. 준비도 판정

- OSM 취득·자동 변환·plain XML·provenance·checksum: **완료**
- 동일 입력/옵션 독립 2회 구조 재현: **통과**
- netconvert 재읽기와 수요 없는 SUMO load: **통과**
- netcheck 실행: **완료, 그러나 전체 edge network 미연결** (422개 component)
- MVP 두 신호 객체의 junction/tlLogic 식별: **통과**
- 차량 중심의 후속 수동 감사 출발점으로 사용: **조건부 가능**
- 정량 평가·RL 학습·보행 포함 MVP에 즉시 사용: **아직 불가** — P0 controlled-link/차로/횡단시설 확인 필요

다음 단계의 최소 보정은 (1) MVP 두 교차로의 물리 방향별 controlled link 대조, (2) 정문 minor-green/turn lane과 U턴 검토, (3) 두 교차로 횡단보도·walkingarea 연결 복원 여부 판단, (4) 9번 출구·공항철도·레드로드 보행 연결 확인, (5) buffer route smoke test 순서다. 자동 병합·guess 옵션은 이 검토 전에 켜지 않는다.
