from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from hongdae_baseline.metrics import VehicleMetrics


TRIPINFO = """<?xml version="1.0" encoding="UTF-8"?>
<tripinfos>
  <tripinfo id="arrived" waitingTime="10" timeLoss="20"/>
  <tripinfo id="unfinished" waitingTime="30" timeLoss="40"/>
</tripinfos>
"""


class VehicleMetricTests(unittest.TestCase):
    def _tripinfo(self, directory: str) -> Path:
        path = Path(directory) / "tripinfo.xml"
        path.write_text(TRIPINFO, encoding="utf-8")
        return path

    def test_vehicle_counts_completion_and_unfinished_population(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            metrics = VehicleMetrics.from_tripinfo(
                self._tripinfo(directory),
                throughput=1,
                generated_vehicle_count=3,
                departed_vehicle_count=2,
                arrived_vehicle_count=1,
                final_network_vehicle_count=1,
                max_queue=4,
                teleport_count=0,
            )
        self.assertEqual(metrics.throughput, metrics.arrived_vehicle_count)
        self.assertEqual(metrics.unfinished_vehicle_count, 2)
        self.assertAlmostEqual(metrics.completion_rate, 1 / 3)
        self.assertEqual(metrics.tripinfo_vehicle_count, 2)
        self.assertEqual(metrics.avg_vehicle_waiting_time, 20.0)

    def test_throughput_must_equal_arrived_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "must equal arrived_vehicle_count"):
                VehicleMetrics.from_tripinfo(
                    self._tripinfo(directory),
                    throughput=2,
                    generated_vehicle_count=3,
                    departed_vehicle_count=2,
                    arrived_vehicle_count=1,
                    final_network_vehicle_count=1,
                    max_queue=0,
                    teleport_count=0,
                )


if __name__ == "__main__":
    unittest.main()
