from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np

from .config import CONTROLLERS


METRICS = (
    "avg_vehicle_waiting_time",
    "p95_vehicle_waiting_time",
    "max_vehicle_waiting_time",
    "avg_time_loss",
    "throughput",
    "max_queue",
    "teleport_count",
    "generated_vehicle_count",
    "departed_vehicle_count",
    "arrived_vehicle_count",
    "unfinished_vehicle_count",
    "completion_rate",
    "final_network_vehicle_count",
)
HIGHER_IS_BETTER = {"throughput", "arrived_vehicle_count", "completion_rate"}
COMPARISONS = (
    ("PPO_V5_170K", "ACTUATED"),
    ("PPO_V5_200K", "ACTUATED"),
    ("PPO_V5_200K", "PPO_V5_170K"),
)


@dataclass(frozen=True)
class PairedData:
    rows: tuple[dict[str, str], ...]
    seeds: tuple[int, ...]
    by_key: dict[tuple[int, str], dict[str, str]]


def read_results(path: Path) -> tuple[dict[str, str], ...]:
    with path.open(encoding="utf-8", newline="") as handle:
        return tuple(csv.DictReader(handle))


def validate_paired_results(
    rows: Iterable[Mapping[str, str]],
    expected_seeds: Sequence[int],
    controllers: Sequence[str] = CONTROLLERS,
) -> PairedData:
    normalized = tuple(dict(row) for row in rows)
    expected = tuple(int(seed) for seed in expected_seeds)
    if len(expected) != len(set(expected)):
        raise ValueError("Expected seeds contain duplicates")
    by_key: dict[tuple[int, str], dict[str, str]] = {}
    for row in normalized:
        key = (int(row["master_seed"]), row["controller"])
        if key in by_key:
            raise ValueError(f"Duplicate result row for seed/controller {key}")
        by_key[key] = row
        if int(float(row["throughput"])) != int(float(row["arrived_vehicle_count"])):
            raise ValueError(f"throughput != arrived_vehicle_count for {key}")
    required_keys = {(seed, controller) for seed in expected for controller in controllers}
    if set(by_key) != required_keys:
        missing = sorted(required_keys - set(by_key))
        extra = sorted(set(by_key) - required_keys)
        raise ValueError(f"Incomplete paired matrix; missing={missing[:5]}, extra={extra[:5]}")
    for seed in expected:
        seed_rows = [by_key[(seed, controller)] for controller in controllers]
        if len({row["route_hash"] for row in seed_rows}) != 1:
            raise ValueError(f"Controllers do not share one route hash for seed {seed}")
        if len({row["sumo_seed"] for row in seed_rows}) != 1:
            raise ValueError(f"Controllers do not share one SUMO seed for seed {seed}")
    return PairedData(normalized, expected, by_key)


def bootstrap_mean_ci(
    values: np.ndarray,
    rng: np.random.Generator,
    samples: int = 10_000,
) -> tuple[float, float]:
    values = np.asarray(values, dtype=np.float64)
    indexes = rng.integers(0, len(values), size=(samples, len(values)))
    boot_means = values[indexes].mean(axis=1)
    low, high = np.percentile(boot_means, [2.5, 97.5])
    return float(low), float(high)


def controller_summary(data: PairedData, bootstrap_seed: int = 20260902) -> list[dict[str, object]]:
    rng = np.random.default_rng(bootstrap_seed)
    output: list[dict[str, object]] = []
    for controller in CONTROLLERS:
        for metric in METRICS:
            values = np.asarray(
                [float(data.by_key[(seed, controller)][metric]) for seed in data.seeds], dtype=np.float64
            )
            low, high = bootstrap_mean_ci(values, rng)
            output.append(
                {
                    "controller": controller,
                    "metric": metric,
                    "n": len(values),
                    "mean": float(np.mean(values)),
                    "median": float(np.median(values)),
                    "std": float(np.std(values, ddof=1)),
                    "bootstrap_95_ci_low": low,
                    "bootstrap_95_ci_high": high,
                }
            )
    return output


def paired_statistics(
    data: PairedData,
    bootstrap_seed: int = 20260903,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    rng = np.random.default_rng(bootstrap_seed)
    differences: list[dict[str, object]] = []
    summaries: list[dict[str, object]] = []
    for candidate, reference in COMPARISONS:
        comparison = f"{candidate}_vs_{reference}"
        for metric in METRICS:
            metric_rows: list[dict[str, object]] = []
            for seed in data.seeds:
                candidate_value = float(data.by_key[(seed, candidate)][metric])
                reference_value = float(data.by_key[(seed, reference)][metric])
                raw_difference = candidate_value - reference_value
                improvement = raw_difference if metric in HIGHER_IS_BETTER else -raw_difference
                row = {
                    "comparison": comparison,
                    "candidate": candidate,
                    "reference": reference,
                    "seed": seed,
                    "metric": metric,
                    "candidate_value": candidate_value,
                    "reference_value": reference_value,
                    "candidate_minus_reference": raw_difference,
                    "improvement_positive_is_better": improvement,
                }
                differences.append(row)
                metric_rows.append(row)
            raw = np.asarray([float(row["candidate_minus_reference"]) for row in metric_rows])
            improvements = np.asarray([float(row["improvement_positive_is_better"]) for row in metric_rows])
            low, high = bootstrap_mean_ci(raw, rng)
            best_index = int(np.argmax(improvements))
            worst_index = int(np.argmin(improvements))
            summaries.append(
                {
                    "comparison": comparison,
                    "candidate": candidate,
                    "reference": reference,
                    "metric": metric,
                    "n": len(raw),
                    "mean_candidate_minus_reference": float(np.mean(raw)),
                    "median_candidate_minus_reference": float(np.median(raw)),
                    "std_candidate_minus_reference": float(np.std(raw, ddof=1)),
                    "bootstrap_95_ci_low": low,
                    "bootstrap_95_ci_high": high,
                    "win_rate": float(np.mean(improvements > 0)),
                    "tie_rate": float(np.mean(improvements == 0)),
                    "max_improvement_seed": int(metric_rows[best_index]["seed"]),
                    "max_improvement": float(improvements[best_index]),
                    "max_worsening_seed": int(metric_rows[worst_index]["seed"]),
                    "max_worsening": float(improvements[worst_index]),
                }
            )
    return differences, summaries


def write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    if not rows:
        raise ValueError(f"Refusing to write empty CSV {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _lookup(rows: Sequence[Mapping[str, object]], comparison: str, metric: str) -> Mapping[str, object]:
    return next(row for row in rows if row["comparison"] == comparison and row["metric"] == metric)


def render_markdown_report(
    data: PairedData,
    controller_rows: Sequence[Mapping[str, object]],
    summaries: Sequence[Mapping[str, object]],
    run_manifest: Path,
) -> str:
    lines = [
        "# 20 paired vehicle-only seed 평가",
        "",
        "## 실행 완전성",
        "",
        f"- paired seed: {len(data.seeds)}개 (`{', '.join(map(str, data.seeds))}`)",
        f"- controller별 평가: {len(data.seeds)}개",
        "- 각 seed에서 controller 간 route hash와 SUMO seed 동일성 검증 완료",
        "- throughput은 모든 행에서 arrived_vehicle_count와 동일함",
        f"- 실행 manifest: `{run_manifest}`",
        "- 대기시간과 time-loss 통계는 평가 종료 시 미완료인 출발 차량도 포함함",
        "",
        "## Controller 요약",
        "",
        "| controller | 평균 대기: mean [95% CI] | median | std | throughput mean [95% CI] | completion mean |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for controller in CONTROLLERS:
        waiting = next(
            row
            for row in controller_rows
            if row["controller"] == controller and row["metric"] == "avg_vehicle_waiting_time"
        )
        throughput = next(
            row for row in controller_rows if row["controller"] == controller and row["metric"] == "throughput"
        )
        completion = next(
            row
            for row in controller_rows
            if row["controller"] == controller and row["metric"] == "completion_rate"
        )
        lines.append(
            "| {controller} | {mean:.3f} [{low:.3f}, {high:.3f}] | {median:.3f} | {std:.3f} | "
            "{flow:.2f} [{flow_low:.2f}, {flow_high:.2f}] | {completion:.4f} |".format(
                controller=controller,
                mean=float(waiting["mean"]),
                low=float(waiting["bootstrap_95_ci_low"]),
                high=float(waiting["bootstrap_95_ci_high"]),
                median=float(waiting["median"]),
                std=float(waiting["std"]),
                flow=float(throughput["mean"]),
                flow_low=float(throughput["bootstrap_95_ci_low"]),
                flow_high=float(throughput["bootstrap_95_ci_high"]),
                completion=float(completion["mean"]),
            )
        )
    lines.extend(
        [
            "",
        "## 핵심 paired 결과",
        "",
        "차이는 `candidate - reference`이다. 대기시간·time loss·queue·미완료 차량은 음수가, throughput·completion rate는 양수가 후보 개선을 뜻한다.",
        "",
        "| 비교 | 평균 대기 차이 (95% CI) | 대기 승률 | throughput 차이 (95% CI) | completion-rate 차이 |",
        "|---|---:|---:|---:|---:|",
        ]
    )
    for candidate, reference in COMPARISONS:
        comparison = f"{candidate}_vs_{reference}"
        waiting = _lookup(summaries, comparison, "avg_vehicle_waiting_time")
        throughput = _lookup(summaries, comparison, "throughput")
        completion = _lookup(summaries, comparison, "completion_rate")
        lines.append(
            "| {label} | {wait:.3f} [{wlow:.3f}, {whigh:.3f}] | {wins:.1%} | "
            "{flow:.3f} [{flow_low:.3f}, {flow_high:.3f}] | {completion:.4f} |".format(
                label=f"{candidate} vs {reference}",
                wait=float(waiting["mean_candidate_minus_reference"]),
                wlow=float(waiting["bootstrap_95_ci_low"]),
                whigh=float(waiting["bootstrap_95_ci_high"]),
                wins=float(waiting["win_rate"]),
                flow=float(throughput["mean_candidate_minus_reference"]),
                flow_low=float(throughput["bootstrap_95_ci_low"]),
                flow_high=float(throughput["bootstrap_95_ci_high"]),
                completion=float(completion["mean_candidate_minus_reference"]),
            )
        )
    lines.extend(["", "## 극단 seed와 해석 보조", ""])
    for candidate, reference in COMPARISONS:
        comparison = f"{candidate}_vs_{reference}"
        waiting = _lookup(summaries, comparison, "avg_vehicle_waiting_time")
        throughput = _lookup(summaries, comparison, "throughput")
        lines.extend(
            [
                f"### {candidate} vs {reference}",
                "",
                f"- 평균 대기 최대 개선 seed: {waiting['max_improvement_seed']} "
                f"(개선량 {float(waiting['max_improvement']):.3f}초)",
                f"- 평균 대기 최대 악화 seed: {waiting['max_worsening_seed']} "
                f"(개선 척도 {float(waiting['max_worsening']):.3f}초)",
                f"- throughput 평균 차이: {float(throughput['mean_candidate_minus_reference']):.3f}대, "
                f"승률 {float(throughput['win_rate']):.1%}",
                "",
            ]
        )
    lines.extend(
        [
            "## 판정",
            "",
            "- PPO 200k는 170k와 20개 중 19개 seed에서 동일했고 seed 101에서만 개선됐다. 평균 대기 차이는 -1.209초지만 bootstrap CI가 [-3.627, 0.000]이고 strict 승률은 5%, tie는 95%다. 따라서 200k를 임시 legacy PPO 기준 모델로 선택할 수는 있으나, 일반적 우월성이 입증된 것은 아니다.",
            "- PPO 200k는 Actuated보다 평균 대기가 2.700초 높고 20%의 seed에서만 이겼다. bootstrap 95% CI [0.170, 5.120]도 후보의 대기시간 열세 방향이다. throughput은 평균 0.7대 낮지만 CI가 0을 포함한다.",
            "- 현재 vehicle-only 기준선의 전체 성능 기준 최선은 Actuated다. 다만 detector 경고가 Actuated를 불리하게 만들 수 있으므로 corrected Actuated를 별도 버전으로 검증하기 전에는 최종 알고리즘 우위 주장에 사용하지 않는다.",
            "",
            "## 산출물",
            "",
            "- `controller_summary.csv`: controller별 평균·중앙값·표준편차·bootstrap 95% CI",
            "- `paired_differences.csv`: seed별 candidate/reference 값과 paired difference",
            "- `paired_summary.csv`: paired 평균·중앙값·표준편차·bootstrap CI·승률·극단 seed",
            "",
            "평균만으로 우위를 판정하지 않으며, CI·seed별 승률·throughput/completion trade-off를 함께 사용한다.",
            "",
        ]
    )
    return "\n".join(lines)
