# 20 paired vehicle-only seed 평가

## 실행 완전성

- paired seed: 20개 (`101, 202, 303, 404, 505, 606, 707, 808, 909, 1010, 1111, 1212, 1313, 1414, 1515, 1616, 1717, 1818, 1919, 2020`)
- controller별 평가: 20개
- 각 seed에서 controller 간 route hash와 SUMO seed 동일성 검증 완료
- throughput은 모든 행에서 arrived_vehicle_count와 동일함
- 실행 manifest: `/Users/ghh7964/Documents/VSCode/hongdae-traffic-control/results/vehicle_only_20_seed_corrected/run_manifest.json`
- 대기시간과 time-loss 통계는 평가 종료 시 미완료인 출발 차량도 포함함

## Controller 요약

| controller | 평균 대기: mean [95% CI] | median | std | throughput mean [95% CI] | completion mean |
|---|---:|---:|---:|---:|---:|
| FIXED_TIME | 31.314 [24.949, 38.677] | 26.726 | 16.273 | 104.50 [99.60, 108.15] | 0.9087 |
| ACTUATED | 16.959 [14.813, 19.352] | 15.321 | 5.344 | 113.05 [111.90, 114.05] | 0.9830 |
| PPO_V5_170K | 20.868 [17.984, 24.332] | 18.954 | 7.427 | 111.25 [108.50, 113.30] | 0.9674 |
| PPO_V5_200K | 19.659 [17.468, 22.408] | 18.606 | 5.818 | 112.35 [110.75, 113.65] | 0.9770 |

## 핵심 paired 결과

차이는 `candidate - reference`이다. 대기시간·time loss·queue·미완료 차량은 음수가, throughput·completion rate는 양수가 후보 개선을 뜻한다.

| 비교 | 평균 대기 차이 (95% CI) | 대기 승률 | throughput 차이 (95% CI) | completion-rate 차이 |
|---|---:|---:|---:|---:|
| PPO_V5_170K vs ACTUATED | 3.909 [0.771, 7.238] | 20.0% | -1.800 [-4.500, 0.200] | -0.0157 |
| PPO_V5_200K vs ACTUATED | 2.700 [0.170, 5.120] | 20.0% | -0.700 [-2.000, 0.550] | -0.0061 |
| PPO_V5_200K vs PPO_V5_170K | -1.209 [-3.627, 0.000] | 5.0% | 1.100 [0.000, 3.300] | 0.0096 |

## 극단 seed와 해석 보조

### PPO_V5_170K vs ACTUATED

- 평균 대기 최대 개선 seed: 1717 (개선량 8.147초)
- 평균 대기 최대 악화 seed: 101 (개선 척도 -24.450초)
- throughput 평균 차이: -1.800대, 승률 25.0%

### PPO_V5_200K vs ACTUATED

- 평균 대기 최대 개선 seed: 1717 (개선량 8.147초)
- 평균 대기 최대 악화 seed: 1313 (개선 척도 -12.035초)
- throughput 평균 차이: -0.700대, 승률 25.0%

### PPO_V5_200K vs PPO_V5_170K

- 평균 대기 최대 개선 seed: 101 (개선량 24.180초)
- 평균 대기 최대 악화 seed: 202 (개선 척도 -0.000초)
- throughput 평균 차이: 1.100대, 승률 5.0%

## 판정

- PPO 200k는 170k와 20개 중 19개 seed에서 동일했고 seed 101에서만 개선됐다. 평균 대기 차이는 -1.209초지만 bootstrap CI가 [-3.627, 0.000]이고 strict 승률은 5%, tie는 95%다. 따라서 200k를 임시 legacy PPO 기준 모델로 선택할 수는 있으나, 일반적 우월성이 입증된 것은 아니다.
- PPO 200k는 Actuated보다 평균 대기가 2.700초 높고 20%의 seed에서만 이겼다. bootstrap 95% CI [0.170, 5.120]도 후보의 대기시간 열세 방향이다. throughput은 평균 0.7대 낮지만 CI가 0을 포함한다.
- 현재 vehicle-only 기준선의 전체 성능 기준 최선은 Actuated다. 다만 detector 경고가 Actuated를 불리하게 만들 수 있으므로 corrected Actuated를 별도 버전으로 검증하기 전에는 최종 알고리즘 우위 주장에 사용하지 않는다.

## 산출물

- `controller_summary.csv`: controller별 평균·중앙값·표준편차·bootstrap 95% CI
- `paired_differences.csv`: seed별 candidate/reference 값과 paired difference
- `paired_summary.csv`: paired 평균·중앙값·표준편차·bootstrap CI·승률·극단 seed

평균만으로 우위를 판정하지 않으며, CI·seed별 승률·throughput/completion trade-off를 함께 사용한다.
