from __future__ import annotations

import unittest

from hongdae_baseline.config import CONTROLLERS
from hongdae_baseline.statistics import paired_statistics, validate_paired_results


def synthetic_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for seed in (101, 202):
        for index, controller in enumerate(CONTROLLERS):
            arrived = 90 + index
            row = {
                "master_seed": str(seed),
                "controller": controller,
                "route_hash": f"hash-{seed}",
                "sumo_seed": str(seed),
                "avg_vehicle_waiting_time": str(30 - index),
                "p95_vehicle_waiting_time": str(50 - index),
                "max_vehicle_waiting_time": str(80 - index),
                "avg_time_loss": str(40 - index),
                "throughput": str(arrived),
                "max_queue": str(20 - index),
                "teleport_count": "0",
                "generated_vehicle_count": "115",
                "departed_vehicle_count": "110",
                "arrived_vehicle_count": str(arrived),
                "unfinished_vehicle_count": str(115 - arrived),
                "completion_rate": str(arrived / 115),
                "final_network_vehicle_count": str(110 - arrived),
            }
            rows.append(row)
    return rows


class PairedStatisticsTests(unittest.TestCase):
    def test_complete_matrix_and_paired_direction(self) -> None:
        data = validate_paired_results(synthetic_rows(), [101, 202])
        differences, summary = paired_statistics(data, bootstrap_seed=7)
        waiting = next(
            row
            for row in summary
            if row["comparison"] == "PPO_V5_200K_vs_ACTUATED"
            and row["metric"] == "avg_vehicle_waiting_time"
        )
        self.assertEqual(waiting["mean_candidate_minus_reference"], -2.0)
        self.assertEqual(waiting["win_rate"], 1.0)
        self.assertTrue(differences)

    def test_route_hash_mismatch_is_rejected(self) -> None:
        rows = synthetic_rows()
        rows[0]["route_hash"] = "wrong"
        with self.assertRaisesRegex(ValueError, "route hash"):
            validate_paired_results(rows, [101, 202])


if __name__ == "__main__":
    unittest.main()
