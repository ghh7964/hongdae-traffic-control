from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.network.audit_hongdae_b import network_statistics
from scripts.network.common import (
    EVALUATION_BBOX,
    EXTRACTION_BBOX,
    REPO_ROOT,
    git_state,
    lane_allows,
    normalize_warning,
    resolve_proj_data,
    resolve_sumo_tools,
    structural_xml_sha256,
    sumo_subprocess_environment,
    weak_components,
)


class NetworkToolingUnitTests(unittest.TestCase):
    def _make_proj_dir(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        (path / "proj.db").write_bytes(b"fixture")
        return path

    def _make_sumo_tree(self, prefix: Path) -> tuple[Path, dict[str, Path]]:
        home = prefix / "share" / "sumo"
        paths = {
            "sumo": prefix / "bin" / "sumo",
            "netconvert": prefix / "bin" / "netconvert",
            "osm_get": home / "tools" / "osmGet.py",
            "netcheck": home / "tools" / "net" / "netcheck.py",
            "vehicle_typemap": home / "data" / "typemap" / "osmNetconvert.typ.xml",
            "pedestrian_typemap": (
                home / "data" / "typemap" / "osmNetconvertPedestrians.typ.xml"
            ),
        }
        for path in paths.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n", encoding="utf-8")
        return home, paths

    def test_fixed_bbox_order_and_separation(self) -> None:
        self.assertEqual(EXTRACTION_BBOX, (126.9168, 37.5510, 126.9296, 37.5605))
        self.assertEqual(EVALUATION_BBOX, (126.9188, 37.5510, 126.9283, 37.5590))
        self.assertLess(EXTRACTION_BBOX[0], EVALUATION_BBOX[0])
        self.assertGreater(EXTRACTION_BBOX[2], EVALUATION_BBOX[2])
        self.assertGreater(EXTRACTION_BBOX[3], EVALUATION_BBOX[3])

    def test_lane_permissions(self) -> None:
        unrestricted = ET.fromstring('<lane id="a"/>')
        vehicle_only = ET.fromstring('<lane id="b" allow="passenger bus"/>')
        no_pedestrian = ET.fromstring('<lane id="c" disallow="pedestrian"/>')
        sidewalk = ET.fromstring('<lane id="d" allow="pedestrian"/>')
        self.assertTrue(lane_allows(unrestricted, "pedestrian"))
        self.assertFalse(lane_allows(vehicle_only, "pedestrian"))
        self.assertFalse(lane_allows(no_pedestrian, "pedestrian"))
        self.assertTrue(lane_allows(sidewalk, "pedestrian"))

    def test_structural_hash_ignores_comments_and_formatting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.xml"
            second = root / "second.xml"
            first.write_text('<net><!-- generated now --><edge id="e" from="a" to="b"/></net>')
            second.write_text('<net>\n  <edge from="a" id="e" to="b"/>\n</net>')
            self.assertEqual(structural_xml_sha256(first), structural_xml_sha256(second))

    def test_warning_normalization_and_components(self) -> None:
        self.assertEqual(
            normalize_warning("Warning: Edge '123' is short (4.5)."),
            "Edge '<id>' is short (<n>).",
        )
        components = weak_components({"a": {"b"}, "b": {"a"}, "c": set()})
        self.assertEqual(components, [["a", "b"], ["c"]])

    def test_network_statistics_from_fixture(self) -> None:
        root = ET.fromstring(
            """<net>
              <edge id="e0" from="a" to="b"><lane id="e0_0" allow="passenger"/></edge>
              <edge id="e1" function="walkingarea"><lane id="e1_0" allow="pedestrian"/></edge>
              <edge id="e2" function="crossing"><lane id="e2_0" allow="pedestrian"/></edge>
              <junction id="a" type="traffic_light"/>
              <junction id="b" type="dead_end" fringe="outer"/>
              <tlLogic id="a" type="static" programID="0" offset="0"><phase duration="10" state="G"/></tlLogic>
              <connection from="e0" to="e0" fromLane="0" toLane="0" tl="a" linkIndex="0"/>
            </net>"""
        )
        network = {
            "root": root,
            "junctions": {element.attrib["id"]: element for element in root.findall("junction")},
            "edges": {element.attrib["id"]: element for element in root.findall("edge")},
            "tl_logics": {element.attrib["id"]: element for element in root.findall("tlLogic")},
            "connections": root.findall("connection"),
        }
        stats = network_statistics(network, "Warning: Edge 'e0' is short (2.0).\n")
        self.assertEqual(stats["junction_count"], 2)
        self.assertEqual(stats["edge_count"], 3)
        self.assertEqual(stats["lane_count"], 3)
        self.assertEqual(stats["tl_logic_count"], 1)
        self.assertEqual(stats["controlled_link_count"], 1)
        self.assertEqual(stats["crossing_count"], 1)
        self.assertEqual(stats["walkingarea_count"], 1)
        self.assertEqual(stats["warning_count"], 1)

    def test_explicit_tool_path_has_highest_priority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home, _paths = self._make_sumo_tree(root / "home-install")
            explicit_sumo = root / "explicit" / "sumo"
            explicit_sumo.parent.mkdir()
            explicit_sumo.write_text("explicit\n", encoding="utf-8")
            tools = resolve_sumo_tools(
                sumo_home=home,
                sumo=explicit_sumo,
                environ={"SUMO_HOME": str(root / "ignored-home")},
                which=lambda _name: None,
                macos_prefix=root / "missing-macos",
            )
            self.assertEqual(tools.sumo, explicit_sumo.resolve())
            self.assertEqual(tools.sumo_home, home.resolve())

    def test_sumo_home_resolves_complete_toolchain(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home, paths = self._make_sumo_tree(root / "install")
            tools = resolve_sumo_tools(
                environ={"SUMO_HOME": str(home)},
                which=lambda _name: None,
                macos_prefix=root / "missing-macos",
            )
            self.assertEqual(tools.sumo, paths["sumo"].resolve())
            self.assertEqual(tools.netconvert, paths["netconvert"].resolve())
            self.assertEqual(tools.osm_get, paths["osm_get"].resolve())
            self.assertEqual(tools.netcheck, paths["netcheck"].resolve())
            self.assertEqual(tools.vehicle_typemap, paths["vehicle_typemap"].resolve())
            self.assertEqual(
                tools.pedestrian_typemap, paths["pedestrian_typemap"].resolve()
            )

    def test_path_fallback_infers_share_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home, paths = self._make_sumo_tree(root / "path-install")
            path_lookup = {
                "sumo": str(paths["sumo"]),
                "netconvert": str(paths["netconvert"]),
            }
            tools = resolve_sumo_tools(
                environ={},
                which=lambda name: path_lookup.get(name),
                macos_prefix=root / "missing-macos",
            )
            self.assertEqual(tools.sumo_home, home.resolve())
            self.assertEqual(tools.osm_get, paths["osm_get"].resolve())
            self.assertEqual(tools.vehicle_typemap, paths["vehicle_typemap"].resolve())

    def test_stale_environment_home_falls_back_to_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home, paths = self._make_sumo_tree(root / "path-install")
            path_lookup = {
                "sumo": str(paths["sumo"]),
                "netconvert": str(paths["netconvert"]),
            }
            tools = resolve_sumo_tools(
                environ={"SUMO_HOME": str(root / "stale-home")},
                which=lambda name: path_lookup.get(name),
                macos_prefix=root / "missing-macos",
            )
            self.assertEqual(tools.sumo_home, home.resolve())

    def test_missing_toolchain_has_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(
                FileNotFoundError, "Unable to resolve a complete SUMO toolchain"
            ):
                resolve_sumo_tools(
                    environ={},
                    which=lambda _name: None,
                    macos_prefix=root / "missing-macos",
                )

    def test_explicit_proj_data_has_highest_priority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            explicit = self._make_proj_dir(root / "explicit")
            environment = self._make_proj_dir(root / "environment")
            result = resolve_proj_data(
                explicit,
                environ={"PROJ_DATA": str(environment)},
                system_candidates=[],
                which=lambda _name: None,
            )
            self.assertEqual(result.path, explicit.resolve())
            self.assertEqual(result.source, "explicit")

    def test_proj_data_environment_variable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            expected = self._make_proj_dir(Path(temporary) / "data")
            result = resolve_proj_data(
                environ={"PROJ_DATA": str(expected)},
                system_candidates=[],
                which=lambda _name: None,
            )
            self.assertEqual(result.path, expected.resolve())
            self.assertEqual(result.source, "environment:PROJ_DATA")

    def test_proj_lib_fallback_after_invalid_proj_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = self._make_proj_dir(root / "lib")
            result = resolve_proj_data(
                environ={
                    "PROJ_DATA": str(root / "missing"),
                    "PROJ_LIB": str(expected),
                },
                system_candidates=[],
                which=lambda _name: None,
            )
            self.assertEqual(result.path, expected.resolve())
            self.assertEqual(result.source, "environment:PROJ_LIB")

    def test_proj_data_inside_sumo_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home, paths = self._make_sumo_tree(root / "install")
            expected = self._make_proj_dir(
                root
                / "install"
                / "framework"
                / "Example.framework"
                / "Versions"
                / "1"
                / "Example"
                / "share"
                / "proj"
            )
            result = resolve_proj_data(
                environ={},
                sumo_home=home,
                sumo_binary=paths["sumo"],
                system_candidates=[],
                which=lambda _name: None,
            )
            self.assertEqual(result.path, expected.resolve())
            self.assertEqual(result.source, "sumo-install")

    def test_explicit_proj_data_without_database_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            empty = Path(temporary) / "empty"
            empty.mkdir()
            with self.assertRaisesRegex(FileNotFoundError, "does not contain proj.db"):
                resolve_proj_data(
                    empty,
                    environ={},
                    system_candidates=[],
                    which=lambda _name: None,
                )

    def test_sumo_subprocess_environment_includes_proj_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home, _paths = self._make_sumo_tree(root / "install")
            proj_data = self._make_proj_dir(root / "proj")
            tools = resolve_sumo_tools(
                sumo_home=home,
                proj_data=proj_data,
                environ={},
                which=lambda _name: None,
                macos_prefix=root / "missing-macos",
                system_proj_candidates=[],
            )
            environment = sumo_subprocess_environment(
                tools, environ={"KEEP": "yes"}, include_pythonpath=True
            )
            self.assertEqual(environment["PROJ_DATA"], str(proj_data.resolve()))
            self.assertEqual(environment["SUMO_HOME"], str(home.resolve()))
            self.assertEqual(environment["PYTHONPATH"], str(home.resolve() / "tools"))
            self.assertEqual(environment["KEEP"], "yes")

    def test_invalid_proj_environment_can_be_explicitly_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = resolve_proj_data(
                environ={"PROJ_DATA": str(Path(temporary) / "missing")},
                system_candidates=[],
                which=lambda _name: None,
            )
            self.assertFalse(result.available)
            self.assertIsNone(result.path)
            self.assertEqual(result.source, "unavailable")

    def test_git_state_records_current_head_without_fixed_expectation(self) -> None:
        state = git_state(repo_root=REPO_ROOT)
        self.assertEqual(len(state["head"]), 40)
        self.assertIsNone(state["expected_head"])
        self.assertIsNone(state["head_matches_expected"])

    def test_optional_expected_head_mismatch_fails(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Expected Git HEAD 0000000"):
            git_state("0000000", repo_root=REPO_ROOT)

    def test_optional_expected_head_accepts_current_prefix(self) -> None:
        current = git_state(repo_root=REPO_ROOT)["head"]
        state = git_state(current[:7], repo_root=REPO_ROOT)
        self.assertTrue(state["head_matches_expected"])


if __name__ == "__main__":
    unittest.main()
