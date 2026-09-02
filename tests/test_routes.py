from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from hongdae_baseline.config import load_config
from hongdae_baseline.route import generate_route, validate_route_edges


ROOT = Path(__file__).resolve().parents[1]


class RouteGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config("corrected_baseline", ROOT)

    def test_same_master_seed_generates_same_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = generate_route(self.config.route_template, Path(directory) / "a.xml", 101)
            second = generate_route(self.config.route_template, Path(directory) / "b.xml", 101)
            self.assertEqual(first.sha256, second.sha256)
            self.assertEqual(first.path.read_bytes(), second.path.read_bytes())

    def test_different_master_seed_generates_different_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = generate_route(self.config.route_template, Path(directory) / "a.xml", 101)
            second = generate_route(self.config.route_template, Path(directory) / "b.xml", 202)
            self.assertNotEqual(first.sha256, second.sha256)

    def test_generated_route_edges_exist_in_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            generated = generate_route(self.config.route_template, Path(directory) / "route.xml", 303)
            validate_route_edges(generated.path, self.config.network)


if __name__ == "__main__":
    unittest.main()

