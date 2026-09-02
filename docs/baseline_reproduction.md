# 단일 교차로 기준선 재현 및 신뢰성 복구

## 현재 결론

코드·자산 수준의 재현 기반과 vehicle-only 기준선 평가를 완료했다. 2026-09-02 현재 공식 macOS pkg의 SUMO 1.27.1로 headless smoke, live route-reset 검증, 20개 paired seed × 4 controller 평가가 통과했다. controller마다 정확히 20개 결과가 있고, 모든 seed에서 route hash와 SUMO seed가 controller 간 동일하다.

처음에는 Codex 프로세스가 삭제된 Homebrew 경로 `/opt/homebrew/opt/sumo/share/sumo`를 `SUMO_HOME`으로 상속했고, 공식 pkg의 실제 경로 중 `EclipseSUMO/` 계층을 탐색하지 못해 미설치로 오판했다. 실제 설치 경로는 `/Library/Frameworks/EclipseSUMO.framework/Versions/1.27.1/EclipseSUMO`였다. 실행기는 이제 유효하지 않은 환경변수보다 공식 pkg를 우선 발견한다.

최종 20-seed 실행 명령은 다음과 같다.

```bash
python3 scripts/evaluate.py paired \
  --mode corrected_baseline \
  --seed-file configs/vehicle_only_20_seeds.json \
  --output results/vehicle_only_20_seed_corrected

python3 scripts/analyze_paired.py \
  --run-dir results/vehicle_only_20_seed_corrected \
  --report docs/vehicle_only_20_seed_report.md
```

실행 상태와 런타임, 자산 checksum, 생성 route, 실제 SUMO route 옵션, 모든 seed는 `results/vehicle_only_20_seed_corrected/run_manifest.json`에 기록된다. 각 route는 115대의 차량을 포함한다. 전체 통계와 paired 차이는 `docs/vehicle_only_20_seed_report.md` 및 실행 결과 디렉터리의 세 통계 CSV에 기록된다.

| Master seed | Route SHA-256 |
|---:|---|
| 101 | `a51108cd860737b2d846973b864f83e1f18c4868b924fa4a5599f1d429317561` |
| 202 | `41a78ffef95268307f27211f7588bcb24e7554d9e1331bd4aadd460683dba615` |
| 303 | `081462ff1c9115376b51349962749aad32a79b3d70aa1ba32a23d2649a10512f` |

## 사용법

단일 controller 평가는 다음 한 명령으로 실행한다. `--route`를 지정하면 해당 route를 그대로 쓰고, 생략하면 master seed로 legacy 대표 route에서 결정론적 차량 route를 생성한다.

```bash
python3 scripts/evaluate.py single \
  --mode corrected_baseline \
  --controller PPO_V5_170K \
  --seed 101
```

네 controller의 paired 비교는 다음 한 명령으로 실행한다.

```bash
python3 scripts/evaluate.py paired \
  --mode corrected_baseline \
  --seeds 101 202 303
```

지원 controller는 `FIXED_TIME`, `ACTUATED`, `PPO_V5_170K`, `PPO_V5_200K`다. 결과 디렉터리에는 `results.csv`, `run_manifest.json`, seed별 route, controller별 `tripinfo.xml`이 저장된다.

환경과 원본 무결성은 다음 명령으로 점검한다.

```bash
python3 scripts/check_environment.py --output results/environment_check.json
python3 scripts/verify_legacy_assets.py
```

테스트는 GUI나 GPU 없이 실행한다. TraCI 통합 테스트는 localhost TCP 포트를 사용하므로 이를 제한하는 샌드박스에서는 로컬 소켓 권한이 필요하다.

```bash
PYTHONPATH=src MPLCONFIGDIR=/tmp/hongdae-mpl \
  XDG_CACHE_HOME=/tmp/hongdae-cache OMP_NUM_THREADS=1 \
  python3 -m unittest discover -s tests -v
```

## 두 재현 모드

| 항목 | `legacy_compatible` | `corrected_baseline` |
|---|---|---|
| PPO 황색 | sumo-rl 기본값 2초 | 원 네트워크와 같은 6초 |
| `max_green=50` | 기존처럼 미강제 | 50초에 다음 phase로 강제 전환 |
| SUMO seed | 기존처럼 `--random` | master seed를 `--seed`에 적용 |
| route | 요청 route를 실제 옵션과 대조 | 요청 route를 실제 옵션과 대조 |
| FIXED/ACTUATED | 별도 controller로 명확히 구분 | 별도 controller로 명확히 구분 |

과거의 잘못된 `FIXED` 라벨 자체는 재현하지 않는다. 대신 manifest에 “과거 FIXED는 native actuated였다”는 제한을 남기고, 현재 실행에서는 실제 static fixed-time과 native actuated를 항상 분리한다.

## faithful extraction

V5 입력은 노트북과 동일한 순서의 21차원 벡터다.

```text
3 phase one-hot
+ 6 incoming-lane density
+ 6 incoming-lane queue
+ 6 incoming-lane max waiting time / 100 (1.0에서 clip)
```

controlled lane 순서는 sumo-rl과 같이 TraCI `getControlledLanes()`의 첫 등장 순서를 유지한다. 실행 시 정적 네트워크에서 link index 순으로 얻은 lane 순서와 TraCI 순서를 대조한다.

보상은 queue와 60초/120초 초과 차량의 계단식 starvation penalty 변화량을 계산한 뒤 `[-5, 5]`로 clip하는 기존 함수를 그대로 분리했다. 평가는 학습하지 않으므로 보상은 결과 지표에 사용하지 않지만, 기준선 의미 보존과 향후 검증을 위해 코드와 테스트를 유지한다.

PPO 추론은 SB3 ZIP의 `policy.pth`를 읽어 tanh MLP와 deterministic categorical argmax를 실행한다. 관측은 연결된 VecNormalize의 mean/variance/epsilon/clip 값으로 정규화한다. 현재 환경의 SB3 `PPO.load()`는 PyTorch 2.12에서 optimizer 역직렬화 중 segmentation fault가 발생했지만, 평가에 필요하지 않은 optimizer state를 읽지 않는 이 로더는 CPU에서 동작한다.

## 발견하고 수정한 기존 가정

1. `RealHongdaeWrapperV5.reset()`은 `route_file`을 바꿨지만 설치된 sumo-rl 1.4.5는 `_route`를 실행 명령에 쓴다. 새 실행기는 SUMO 시작 직후 `simulation.getOption("route-files")`를 읽어 요청한 절대경로와 다르면 실패한다.
2. 기존 100회 평가의 `randomTrips.py`에는 seed가 없어 동일 수요가 반복됐을 가능성이 높다. corrected 모드는 master seed를 route와 SUMO에 명시한다.
3. 기존 PPO는 sumo-rl 기본 `sumo_seed="random"`, 기준선은 SUMO 기본 seed를 사용했다. corrected paired 평가는 모든 controller에 같은 route와 SUMO seed를 전달한다.
4. 기존 `FIXED`는 target TLS가 `type="actuated"`인 네트워크를 그대로 실행했다. 새 `FIXED_TIME`은 네트워크의 nominal phase duration을 static program으로 설치하고, `ACTUATED`는 native program을 그대로 둔다.
5. 네트워크 target TLS의 모든 황색은 6초지만 기존 PPO 환경은 sumo-rl 기본 2초를 사용했다. 두 값은 모드 설정에 명시됐다.
6. sumo-rl 1.4.5 소스는 `max_green`이 무시된다고 명시한다. corrected PPO 상태기계는 50초에 실제 황색 전환을 시작하며 강제 횟수를 manifest에 기록한다.
7. 170k는 별도 validation 교통 지표가 아니라 학습 중 reward로 선택됐다. asset 역할을 `selected_legacy`, 200k를 `training_end`로 기록하고 서로 다른 정규화 통계와 연결했다.
8. 노트북의 Google Drive 절대경로와 전역 상태를 config, CLI, 실행별 출력 디렉터리로 대체했다.

## 기존 결과와 수정 평가의 차이

노트북이 저장한 기존 100회 평균은 다음과 같다.

| 기존 라벨 | 평균 대기 | 평균 time loss | 최대 대기 |
|---|---:|---:|---:|
| V5 | 57.62초 | 83.57초 | 244.92초 |
| V4 | 46.75초 | 74.38초 | 366.87초 |
| `FIXED` | 86.28초 | 104.51초 | 319.00초 |

이 값은 독립적인 100개 수요, 동일 SUMO seed, 실제 fixed-time 비교라는 조건을 충족하지 않으므로 corrected 결과와 직접 비교할 신뢰 가능한 기준값으로 사용하지 않는다. 특히 기존 `FIXED` 행은 actuated 결과로 해석해야 한다.

수정 평가의 3-seed 평균은 다음과 같다.

| Controller | 평균 대기 | 대기 p95 | 최대 대기 | 평균 time loss | Throughput | 최대 queue | Teleport |
|---|---:|---:|---:|---:|---:|---:|---:|
| FIXED_TIME | 46.85초 | 178.40초 | 225.33초 | 61.16초 | 94.33 | 7.00 | 0.33 |
| ACTUATED | 19.50초 | 69.50초 | 93.00초 | 35.97초 | 113.67 | 6.67 | 0.00 |
| PPO_V5_170K | 25.52초 | 103.18초 | 133.33초 | 40.48초 | 104.33 | 7.00 | 0.00 |
| PPO_V5_200K | **17.46초** | **56.17초** | **85.33초** | **33.13초** | 111.67 | 7.00 | 0.00 |

FIXED_TIME seed 101은 teleport 1회와 낮은 throughput 때문에 평균이 크게 나빠졌다. 동일 seed의 별도 single smoke와 paired 행은 같은 지표를 내어 재실행 일관성을 확인했다.

## 초기 3-seed의 170k 대 200k 및 기준선 비교

초기 corrected 평가에서는 200k가 170k보다 나아 보였다. 200k는 170k 대비 평균 대기 31.6%, p95 대기 45.6%, 최대 대기 36.0%, 평균 time loss 18.1%가 낮고 throughput은 약 7.0% 높았다. 그러나 seed 202와 303에서는 두 모델 지표가 같고 차이는 seed 101에서만 발생했다. 이 초기 해석은 아래 20-seed 결과로 대체한다.

200k는 Actuated 대비 평균 대기 10.5%, p95 대기 19.2%, 최대 대기 8.2%, 평균 time loss 7.9%가 낮지만 throughput은 1.8% 낮았다. seed별로는 101과 202에서 평균 대기가 Actuated보다 조금 높고, 303에서 크게 낮아 전체 평균을 개선했다. 170k는 평균적으로 Actuated보다 나빴다. 두 PPO 모두 Fixed-time보다는 우수했다.

## 최종 20-seed 판정

20-seed에서는 Actuated의 평균 차량 대기가 16.959초로 가장 낮았고 PPO 200k 19.659초, PPO 170k 20.868초, Fixed-time 31.314초 순이었다. PPO 200k는 Actuated보다 평균 대기가 2.700초 높았으며 paired bootstrap 95% CI는 [0.170, 5.120], seed별 대기 승률은 20%였다. 따라서 초기 3-seed에서 보인 PPO 200k의 Actuated 우위는 재현되지 않았다.

PPO 200k는 170k보다 평균 대기가 1.209초 낮았지만, 20개 중 19개 seed가 완전히 같고 seed 101 한 개에서만 개선됐다. paired CI는 [-3.627, 0.000], strict 승률 5%, tie 95%다. 200k를 `training_end`의 임시 legacy PPO 대표 모델로 선택하는 것은 가능하지만, 일반적 우월성이 입증됐다고 주장하지 않는다.

상세 평균·중앙값·표준편차·bootstrap CI, seed별 paired difference, throughput/completion trade-off는 `docs/vehicle_only_20_seed_report.md`에 기록했다.

## SUMO 설치 및 경로 진단

공식 SUMO 1.27.1 pkg는 이미 설치돼 있다. 확인된 바이너리는 다음과 같다.

```text
/Library/Frameworks/EclipseSUMO.framework/Versions/1.27.1/EclipseSUMO/bin/sumo
/Library/Frameworks/EclipseSUMO.framework/Versions/1.27.1/EclipseSUMO/bin/netconvert
/Library/Frameworks/EclipseSUMO.framework/Versions/1.27.1/EclipseSUMO/bin/duarouter
```

설치 위치를 확인한 뒤, framework 기본 배치라면 현재 shell에 다음을 설정한다.

```bash
export SUMO_HOME=/Library/Frameworks/EclipseSUMO.framework/Versions/Current/EclipseSUMO/share/sumo
export PATH=/Library/Frameworks/EclipseSUMO.framework/Versions/Current/EclipseSUMO/bin:$PATH
export PYTHONPATH=$SUMO_HOME/tools:$PYTHONPATH

sumo --version
netconvert --version
duarouter --version
python3 -c 'import traci, sumolib; print(traci.__file__); print(sumolib.__file__)'
```

참고: [SUMO 공식 다운로드](https://sumo.dlr.de/docs/Downloads.php), [SUMO 1.27.1 배포 디렉터리](https://sumo.dlr.de/releases/1.27.1/), [SUMO_HOME/PATH 설정](https://sumo.dlr.de/docs/Basics/Basic_Computer_Skills.html).

## 남은 위험

- 현재 결정론적 generator는 legacy 대표 route에서 차량 경로만 sampling한다. 기존 평가의 보행자 randomTrips 및 택시 stop 수요와 같지 않다. 본 단계에서는 공정한 차량 기준선 복구를 우선하며 이 차이를 manifest에 기록한다.
- 개발 환경 Python 3.13.2, NumPy 2.3.3, PyTorch 2.12.0은 legacy 환경과 다르다. Python 도구는 공식 pkg 경로에서 읽지만, 전역 pip의 `traci/sumolib` 1.26.0 배포 metadata가 버전 문자열에 노출된다.
- SUMO 1.26.0 생성 네트워크는 1.27.1에서 정상 로드됐지만, target actuated TLS의 linkIndex 10~12에 controlling detector가 없다는 SUMO 경고가 매 실행 발생한다. 세 link는 같은 차량 진입 차로의 직진·좌회전·유턴이며, 방향별 녹색 phase가 분리되어 기본 detector를 녹색 연장에 쓰지 못한다. 상세 감사는 `docs/actuated_detector_audit.md`에 있다.
- 6초 황색이 5초 PPO decision interval보다 길다. corrected 상태기계는 황색 중 새 action을 보류하고 6초를 보장한다. 이는 2초 황색으로 학습한 정책에 대한 의도적인 교정이며 성능 분포를 바꿀 수 있다.
- max-green 강제 시 정책이 같은 phase를 계속 요구하면 다음 phase를 순환 선택한다. 안전 제약으로 명시적이지만 학습 시 없던 동작이다.
- `tripinfo-output.write-unfinished=true`로 500초 종료 시 미완료 차량도 대기/time-loss 통계에 포함하고, throughput은 실제 도착 차량만 별도로 센다.

## 다음 단계 진입 판단과 사용자 결정

20개 vehicle-only paired seed 기준선 게이트는 통과했다. 기준선 복구 단계를 마감하고 4~6주 기능 단계로 넘어갈 수 있으나 다음 결정은 명시적으로 남긴다.

1. corrected validation 수요를 현재의 vehicle-only representative sampling으로 확정할지, seed가 고정된 차량+보행자 randomTrips로 확장할지 결정한다.
2. 1.27.1 결과를 개발 기준으로 확정할지, 별도 1.27.0 환경에서 strict legacy 비교도 수행할지 결정한다.
3. 현재 Actuated를 `legacy_network_actuated`로 유지하고, detector 제약을 수정한 별도 `corrected_actuated` 네트워크/logic을 만들지 결정한다. 수정하면 원본과 분리된 checksum 자산으로 관리해야 한다.
4. PPO 200k를 임시 legacy PPO 대표 모델로 고정하되, SB3 parity fixture가 들어오기 전에는 수동 추론 parity를 미해결 검증 항목으로 유지할지 결정한다.

이 결정을 기록하면 기준선 복구 단계는 종료하고 다음 단계로 넘어갈 수 있다. 다중 교차로, 보행자 제어, TrafficSnapshot 전체 구조, YOLO 통합, PPO 재학습은 여전히 이번 변경 범위에 포함하지 않았다.
