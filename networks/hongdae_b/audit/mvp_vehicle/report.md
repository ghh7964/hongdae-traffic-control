# MVP 차량 네트워크·controlled-link 정밀 감사

감사일: `2026-09-04`
범위: 자동 생성본의 차량 구조 감사만 수행했다. raw OSM, generated auto network,
`legacy/`, `results/`, corrected network는 수정하지 않았다. 보행자 crossing, 영구 수요,
실제 신호 주기 추정, RL 구현은 범위 밖이다.

## 1. 기준 상태와 provenance

- 감사 시작 시 Git HEAD: `79f1ba49f45488e3764d9aaea53da0d4d487b8d9`; 지정 기준 commit과 일치:
  `True`.
- `main` 대 `origin/main`: ahead `0` / behind
  `0`.
- 시작 전 작업 트리: **clean** (감사 실행 전 직접 확인). 이 보고서 생성 후 변경은 아래 산출물과
  감사 스크립트/테스트뿐이다.
- raw SHA-256: `16c432a9591b4ab53c471633dd31967239031f49b20ceae9ff7560baf1a8fc61` (baseline 일치:
  `True`).
- generated net SHA-256: `c17729eb755e88e858ea6b5ad13332dd0bf3ecd17cd7673186037971555ec8f1` (baseline 일치:
  `True`).
- SUMO: `Eclipse SUMO sumo 1.27.1` at `/Library/Frameworks/EclipseSUMO.framework/Versions/1.27.1/EclipseSUMO/bin/sumo`.
- netconvert: `Eclipse SUMO netconvert 1.27.1` at `/Library/Frameworks/EclipseSUMO.framework/Versions/1.27.1/EclipseSUMO/bin/netconvert`.
- duarouter: `Eclipse SUMO duarouter 1.27.1`.
- `legacy/`와 `results/`의 감사 전/후 tree digest가 각각 동일하고 Git path status도 비어 있다.

## 2. 투영 정확성 판정

`<location>`은 `+proj=utm +zone=52 +ellps=WGS84 +datum=WGS84 +units=m +no_defs`, offset
`-315716.05,-4157792.18`, original boundary
`126.912213,37.547860,126.932423,37.566849`를 기록한다. 설치된 Python에는 `pyproj`가 없어
`sumolib.net.convertXY2LonLat`를 그대로 호출할 수 없었지만, 그 API가 감싸는 것과 동등한
**SUMO가 링크한 공식 PROJ C API**를 같은 projection string/offset으로 호출했다.

| Junction | SUMO XY | inverse lon/lat | raw OSM lon/lat | error (m) |
|---|---|---|---|---:|
| 2959081059 | [928.79, 437.55] | [126.9242675463, 37.5528519164] | [126.9242676, 37.5528519] | 0.005073 |
| 3034197250 | [698.41, 695.49] | [126.9215964719, 37.5551294758] | [126.9215965, 37.5551295] | 0.003660 |

최대 오차는 `0.005073 m`이다. 올바른 `proj.db`는
`/Library/Frameworks/EclipseSUMO.framework/Versions/1.27.1/EclipseSUMO/framework/EclipseSUMO.framework/Versions/1.27.1/EclipseSUMO/share/proj`이며 `PROJ_DATA`로 이 디렉터리를 지정한 net reload에서는
`pj_obj_create`가 재발하지 않았다. 따라서 기존 경고는 **환경 resource lookup 경고이며 실제
네트워크 좌표 오류가 아니다**. 방향 판정을 계속하기에 충분하다.

승인 후 `scripts/network/common.py`에 이식 가능한 resolver를 추가했다. 명시 인자 → 유효한
`PROJ_DATA` → 유효한 `PROJ_LIB` → 해석된 SUMO 설치/bundle → 표준 시스템 위치 순서로
`proj.db`를 확인하며, build/audit의 SUMO 계열 subprocess는 공통 환경 생성기를 사용한다.
기존 실행 당시 경고와 위 오차 수치는 역사적 감사 사실로 그대로 유지한다.

## 3. controlled links 전수 결과

| 교차로 | connection | link group | duplicate link-index group | movement |
|---|---:|---:|---:|---|
| 홍익대학교 정문 앞 | 17 | 17 | 0 | {'left': 4, 'right': 4, 'straight': 5, 'u_turn': 4} |
| 홍대입구역사거리 | 19 | 19 | 0 | {'left': 4, 'right': 3, 'straight': 10, 'u_turn': 2} |

두 TLS 모두 한 link index에 여러 실제 connection이 묶인 경우는 없다. 전수 36개 connection의
lane/via/phase state/OSM 근거는 `controlled_links.csv`와 `controlled_links.json`에 있다.

방향은 UTM node geometry에서 얻은 진북 기준 방위각을 가장 가까운 cardinal로 축약했다. 대각선
형상의 정문 교차로는 같은 cardinal bucket에 서로 다른 남동·남서 접근이 들어갈 수 있으므로 CSV의
근거 문장에 octant와 각도를 함께 보존했다.

| 교차로 | 추정 접근 cardinal | movement connection 수 |
|---|---|---|
| 홍익대학교 정문 앞 | east | left 1, right 1, straight 1, u_turn 1 |
| 홍익대학교 정문 앞 | north | left 1, right 1, straight 1, u_turn 1 |
| 홍익대학교 정문 앞 | south | left 2, right 2, straight 3, u_turn 2 |
| 홍대입구역사거리 | north | left 2, straight 1, u_turn 1 |
| 홍대입구역사거리 | south | right 2, straight 3, u_turn 1 |
| 홍대입구역사거리 | west | left 2, right 1, straight 6 |

모든 connection에서 SUMO `dir`과 기하학적 회전 부호가 일치했다. 이는 형상 분류의 근거일 뿐
실제 허용 근거는 아니다. 관련 OSM way에는 `turn:lanes`가 없고 두 junction/way를 멤버로 하는
restriction relation도 raw snapshot에 없다. 태그 부재를 허용으로 해석하지 않았다.

## 4. U턴 6개

| 교차로 | link | connection | 접근 | passenger 사용 | 판정 |
|---|---:|---|---|---|---|
| 홍익대학교 정문 앞 | 3 | `-218976037#0_2 → 218976037#0_2` | east | yes | 자동 생성됐지만 근거 없음 |
| 홍익대학교 정문 앞 | 7 | `-168874251#0_0 → 168874251#0_0` | south | no | 자동 생성됐지만 근거 없음 |
| 홍익대학교 정문 앞 | 12 | `333681731#5_1 → -333681731#5_0` | south | yes | 자동 생성됐지만 근거 없음 |
| 홍익대학교 정문 앞 | 16 | `299767124#4_0 → -299767124#4_0` | north | yes | 자동 생성됐지만 근거 없음 |
| 홍대입구역사거리 | 5 | `-218976035#0_1 → 218976035#0_0` | south | yes | 자동 생성됐지만 근거 없음 |
| 홍대입구역사거리 | 18 | `-254749392_2 → 254749392_2` | north | yes | 자동 생성됐지만 근거 없음 |

6개 모두 `dir=t`와 형상은 일치하지만 허용·금지를 확정할 OSM 태그나 외부 표지/노면표시 자료가
없다. 따라서 전부 **자동 생성됐지만 근거 없음**으로 분류하고 삭제하지 않았다. 정문 link 7은
service lane의 `allow=pedestrian delivery bicycle` 때문에 승용차는 사용할 수 없다. 현장 또는
날짜가 확인되는 로드뷰 검토 전에는 나머지도 임의 허용/금지하지 않는다.

## 5. minor-green 경고 2개

1. `299767124#4 -> 218976037#0`: **자동 신호 프로그램의 permissive-green 문제에 가장 가깝고,
   실제 좌회전 차로 누락이 2차 후보**다. 유일한 승용차 lane 0이 right/straight/left/U-turn을
   모두 담당하며 link 15는 phase 0에서 `g`다. raw way/299767124에는 `lanes`, `turn:lanes`,
   `maxspeed`가 모두 없고 typemap 결과 속도는 27.78 m/s다. 기하학은 명확한 left이므로 회전
   방향 오판 가능성은 낮고, connection 삭제 근거는 없다.
2. `254749392 -> 515592172#0`: 같은 조합에 가깝다. lane 2가 straight/left/U-turn을 공유하고
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
  `[{"phase": 0, "first": 5, "second": 15, "type": "G-g"}, {"phase": 0, "first": 5, "second": 16, "type": "G-g"}, {"phase": 0, "first": 13, "second": 6, "type": "G-g"}, {"phase": 0, "first": 14, "second": 6, "type": "G-g"}, {"phase": 0, "first": 14, "second": 7, "type": "G-g"}, {"phase": 4, "first": 2, "second": 11, "type": "G-G"}]`.
- 역사거리 phase 4에는 U-turn link 5 (`g`)와 right-turn link 6 (`G`)의 foe pair 1개가 있다.
  전체 pair: `[{"phase": 4, "first": 6, "second": 5, "type": "G-g"}]`.
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
| gate_south_to_station | `333681731#0` | `333681721#3` | True | True | -299767124#4 / -218976035#0 | success | arrived |
| station_to_gate_south | `333681730#0` | `-333681731#0` | True | True | 218976035#0 / 299767124#4 | success | arrived |
| yanghwa_west_to_gate | `333681730#0` | `218976037#2` | True | True | 218976035#0 / 299767124#4 | success | arrived |
| yanghwa_east_to_gate | `515585529#0` | `218976037#2` | True | True | 218976035#0 / 299767124#4 | success | arrived |
| worldcup_to_gate | `-515836541#3` | `218976037#2` | True | True | 218976035#0 / 299767124#4 | success | arrived |
| gate_to_worldcup | `333681731#0` | `515836541#3` | True | True | -299767124#4 / -218976035#0 | success | arrived |
| outer_pass_through | `332222851#2` | `336580316#1` | False | False | — / — | success | arrived |

- route error 0, disconnected 0, replacement/repair 0, reroute 0.
- loaded/inserted/arrived: 7/7/7; 종료 시 running 0, waiting 0.
- teleport `0`, collision
  `0`, emergency stop
  `0`, emergency braking
  `0`.
- 최장 waitingTime은 `87.0s`이고 모든
  차량이 최대 236초에 도착해 무한 대기 징후가 없다.

이는 구조적 통행 가능성만 확인한다. 자동 신호의 성능·현실성 평가는 아니다.

## 8. 보정 명세

| correction | 등급 | 대상 | 제안 |
|---|---|---|---|
| MV-PROJ-001 | 필수 | audit/build execution environment | Set PROJ_DATA to the versioned SUMO bundle share/proj directory in the reproducible audit/build wrapper. |
| MV-GATE-LANE-001 | 필수 | junction 2959081059; edge 299767124#4 lane 0; link 15 and sibling links 13,14,16 | First verify lane arrows/count and legal speed. If confirmed, encode lane count/speed and explicit lane-to-lane connections in a plain edge/connection patch or deterministic script; otherwise retain and mark unresolved. |
| MV-ADJ-LANE-001 | 권장 | junction 11059771900; edge 254749392 lane 2; links 9,10,11 | Verify physical lane arrows, legal speed, and U-turn rule; then encode only confirmed speed/lane/connection facts in a plain patch or deterministic script. |
| MV-UTURN-GATE-001 | 보류 | junction 2959081059 U-turn links 3,7,12,16 | Make no topology change until dated field/street-view evidence is captured; then add explicit plain connection deletions/retentions in a reviewed patch. |
| MV-UTURN-STATION-001 | 보류 | junction 3034197250 U-turn links 5,18 | Make no topology change until dated field/street-view evidence is captured; then patch connection/tll definitions reproducibly. |
| MV-TLS-STRUCT-001 | 필수 | junction 2959081059 phase 4; links 2 and 11 | Before any operational use, validate internal paths/vehicle classes and generate a reviewed tll patch. Do not treat the six-phase auto program as real timing. |
| MV-STATION-GEOM-001 | 권장 | junction 3034197250 approaches ±218976035#0 and ±254749392 | Inspect plain-node junction shapes and adjacent split nodes; if queue storage is materially wrong, apply an explicit node-shape/edge patch rather than editing auto.net.xml. |
| MV-SPEED-001 | 필수 | MVP and adjacent primary/secondary/tertiary approaches lacking maxspeed | Obtain authoritative/dated speed evidence, then apply a deterministic plain edge speed patch. Do not guess a Seoul-wide value. |

합계: 필수 4, 권장 2, 보류 2.
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
  (확인일 `2026-09-04`).
- OSM 데이터: © OpenStreetMap contributors, ODbL 1.0. 원본 snapshot은
  `networks/hongdae_b/raw/hongdae_b_20260903_bbox.osm.xml`이다.

생성 파일: `report.md`, `controlled_links.csv`, `controlled_links.json`, `edge_mapping.csv`,
`phase_matrix.csv`, `uturn_audit.csv`, `route_smoke.json`, `correction_plan.json`.
