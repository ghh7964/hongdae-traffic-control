# Actuated detector 경고 감사

## 결론

SUMO 1.27.1의 `linkIndex 10, 11, 12 has no controlling detector` 경고는 보행자 신호나 장식용 보조 신호가 아니라 **동일한 차량 진입 차로를 공유하는 직진·좌회전·유턴 연결**에 관한 것이다. 세 연결은 모두 신호에 의해 서비스되지만, SUMO의 기본 actuated detector가 그 차로의 녹색 연장을 결정하는 데 사용되지 못한다.

원인은 `333681731#4_1` 차로의 세 연결이 서로 다른 녹색 phase로 분리되어 있기 때문이다. SUMO는 기본적으로 한 detector 차로의 모든 연결이 같은 phase에서 무조건 녹색(`G`)일 때만 그 detector를 phase 연장에 사용한다. 이 네트워크에서는 그런 phase가 없다.

## 네트워크 근거

대상 TLS는 `2959081059`이고 state 문자열 길이는 17이다. `hongdae.net.xml`의 connection과 phase state를 대응하면 다음과 같다.

| linkIndex | from lane | to lane | dir | 이동류 | 녹색 phase | 황색 phase |
|---:|---|---|---|---|---:|---:|
| 10 | `333681731#4_1` | `218976037#0_1` | `s` | 직진 차량 | 2 (`G`) | 3 (`y`) |
| 11 | `333681731#4_1` | `-299767124#3_0` | `l` | 좌회전 차량 | 4 (`G`) | 5 (`y`) |
| 12 | `333681731#4_1` | `-333681731#4_0` | `t` | 유턴 차량 | 4 (`G`) | 5 (`y`) |

진입 edge `333681731#4`는 `highway.tertiary`이고 lane 1은 일반 도로 차량을 허용한다. 세 도착 edge도 `highway.tertiary` 또는 `highway.secondary`이다. 따라서 세 link는 보행자·철도·보조 신호가 아니라 일반 차량 이동류다.

대상 phase는 다음과 같다.

- phase 2: `GGrrrrrrGGGrrrrrr`, `minDur=10`, `maxDur=50`
- phase 4: `rrGGrrrrrrrGGrrrr`, `minDur=10`, `maxDur=50`

phase 2에서는 link 10만 `G`이고 link 11·12는 적색이다. phase 4에서는 link 11·12만 `G`이고 link 10은 적색이다. 즉, 공유 차로의 모든 연결이 동시에 `G`가 되는 phase가 없다.

## SUMO 동작과 경고의 의미

SUMO의 actuated 신호는 detector에서 측정한 차량 간 gap으로 녹색 phase를 연장한다. 공식 문서에 따르면 자동 detector는 각 진입 차로에 배치되지만, 한 detector 차로에서 나가는 **모든 connection이 해당 phase에서 무조건 녹색(`G`)**일 때만 그 phase의 actuation에 사용된다. 앞 차량의 진행 방향이 적색인 상황에서 불필요하게 phase를 연장하지 않기 위한 규칙이다. 이 조건을 만족하지 못하면 `has no controlling detector` 경고가 발생한다.

근거: [SUMO Traffic Lights — Actuated detectors](https://sumo.dlr.de/docs/Simulation/Traffic_Lights.html#detectors), [SUMO Traffic Lights — signal/link index](https://sumo.dlr.de/docs/Simulation/Traffic_Lights.html#signal_state_definitions)

## 기준선 성능에 미칠 수 있는 영향

이 경고는 해당 이동류가 통과하지 못한다는 뜻은 아니다. phase 2와 phase 4는 최소 녹색시간 동안 정상적으로 실행되고, 다른 사용 가능한 detector가 같은 phase를 연장할 수도 있다. 그러나 `333681731#4_1`의 직진·좌회전·유턴 수요 자체는 기본 detector를 통해 녹색 연장을 요청하지 못한다.

따라서 다음 편향 가능성이 있다.

- 이 차로에 차량이 남아 있어도 다른 detector의 요청이 없으면 phase가 `minDur` 이후 일찍 종료될 수 있다.
- 방향별 대기열이 큰 seed에서 Actuated의 대기시간·미완료 차량 수가 불리해질 수 있다.
- PPO는 직접 phase를 선택하므로 같은 detector 제약을 받지 않는다. 따라서 PPO 대 Actuated 비교에서 Actuated가 구조적으로 불리해질 가능성을 배제할 수 없다.
- 영향의 크기는 실제 route별로 이 세 movement를 사용하는 차량 수와 다른 detector의 동시 요청에 따라 달라지므로, 경고만으로 수치 효과를 확정할 수 없다.

## corrected network 필요성 판단

현재 20-seed vehicle-only 결과는 **원본 네트워크의 actuated 프로그램을 있는 그대로 사용한 legacy 기준선**으로 유지한다. 이번 단계에서는 원본 및 파생 네트워크를 수정하지 않는다.

다만 최종 졸업프로젝트의 “공정한 real actuated 기준선”에는 별도 버전의 corrected network 또는 additional TLS logic이 필요할 가능성이 높다. 후보는 다음과 같다.

1. 직진과 좌회전·유턴을 물리적으로 분리한 전용 진입 차로 구성
2. 별도 additional TLS logic에서 `jam-threshold`를 양수로 설정하여 detector 사용 조건 변경
3. `build-all-detectors` 또는 검증된 custom detector/logic 적용

각 방법은 신호 제어 의미를 바꾸므로 원본 파일을 덮어쓰지 않고 새 자산·checksum·버전명으로 관리해야 한다. 수정 전후에는 link별 detector activation, phase duration, movement별 통과량을 같은 paired route에서 비교해야 한다.

## 감사 범위와 남은 불확실성

- 확인 완료: link 10/11/12의 from/to lane, 방향, 차량 이동류 여부, 서비스 phase, 경고 발생 구조
- 확인 완료: 원본 네트워크는 수정하지 않음
- 미측정: detector 제약을 제거했을 때 Actuated 성능이 얼마나 변하는지
- 다음 검증: GUI의 `show detectors` 또는 TLS detector output으로 seed별 activation과 phase 연장 시간을 기록
