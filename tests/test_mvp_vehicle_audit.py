from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from scripts.network.audit_mvp_vehicle import (
    cardinal,
    controlled_link_records,
    decode_foes,
    geometry_movement,
    parse_net,
    parse_osm,
    phase_records,
    projection_audit,
    route_crosses_junction,
)
from scripts.network.common import NETWORK_ROOT, resolve_sumo_tools


class MVPVehicleAuditUnitTests(unittest.TestCase):
    def test_cardinal_bearings(self) -> None:
        self.assertEqual(cardinal(0), "north")
        self.assertEqual(cardinal(91), "east")
        self.assertEqual(cardinal(181), "south")
        self.assertEqual(cardinal(271), "west")

    def test_geometry_movement_uses_clockwise_right(self) -> None:
        self.assertEqual(geometry_movement(0, 90)[1], "right")
        self.assertEqual(geometry_movement(0, 270)[1], "left")
        self.assertEqual(geometry_movement(5, 185)[1], "u_turn")
        self.assertEqual(geometry_movement(350, 5)[1], "straight")

    def test_sumo_foe_bits_are_decoded_right_to_left(self) -> None:
        junction = ET.fromstring(
            '<junction><request index="0" foes="001"/><request index="1" foes="100"/></junction>'
        )
        self.assertEqual(decode_foes(junction), {0: {0}, 1: {2}})

    def test_route_junction_crossing_requires_transition(self) -> None:
        root = ET.fromstring(
            """<net>
            <junction id="a" x="0" y="0"/><junction id="j" x="1" y="0"/>
            <junction id="b" x="2" y="0"/><junction id="c" x="3" y="0"/>
            <edge id="in" from="a" to="j"><lane id="in_0" index="0"/></edge>
            <edge id="out" from="j" to="b"><lane id="out_0" index="0"/></edge>
            <edge id="else" from="b" to="c"><lane id="else_0" index="0"/></edge>
            </net>"""
        )
        net = {"edges": {edge.attrib["id"]: edge for edge in root.findall("edge")}}
        self.assertTrue(route_crosses_junction(["in", "out"], "j", net))
        self.assertFalse(route_crosses_junction(["out", "else"], "j", net))

    def test_baseline_controlled_link_counts_and_uturns(self) -> None:
        net = parse_net(NETWORK_ROOT / "generated" / "hongdae_b.auto.net.xml")
        osm = parse_osm(NETWORK_ROOT / "raw" / "hongdae_b_20260903_bbox.osm.xml")
        records, summaries = controlled_link_records(net, osm)
        self.assertEqual(len(records), 36)
        self.assertEqual(summaries["2959081059"]["connection_count"], 17)
        self.assertEqual(summaries["3034197250"]["connection_count"], 19)
        self.assertEqual(sum(row["movement_class"] == "u_turn" for row in records), 6)
        self.assertFalse(summaries["2959081059"]["duplicate_link_index_groups"])
        self.assertFalse(summaries["3034197250"]["duplicate_link_index_groups"])

    def test_baseline_phase_foe_findings(self) -> None:
        net = parse_net(NETWORK_ROOT / "generated" / "hongdae_b.auto.net.xml")
        _rows, summaries = phase_records(net)
        self.assertTrue(summaries["2959081059"]["all_links_serviced"])
        self.assertTrue(summaries["3034197250"]["all_links_serviced"])
        gate_pairs = summaries["2959081059"]["simultaneous_foe_pairs"]
        self.assertIn(
            {"phase": 4, "first": 2, "second": 11, "type": "G-G"}, gate_pairs
        )
        station_pairs = summaries["3034197250"]["simultaneous_foe_pairs"]
        self.assertIn(
            {"phase": 4, "first": 6, "second": 5, "type": "G-g"}, station_pairs
        )

    def test_projection_error_regression_for_both_mvp_junctions(self) -> None:
        try:
            tools = resolve_sumo_tools()
        except FileNotFoundError as error:
            self.skipTest(str(error))
        if tools.proj_data is None:
            self.skipTest("No offline proj.db is available")
        net_path = NETWORK_ROOT / "generated" / "hongdae_b.auto.net.xml"
        net = parse_net(net_path)
        osm = parse_osm(NETWORK_ROOT / "raw" / "hongdae_b_20260903_bbox.osm.xml")
        result = projection_audit(net_path, net, osm, tools)
        errors = {point["junction_id"]: point["distance_error_m"] for point in result["points"]}
        self.assertAlmostEqual(errors["2959081059"], 0.005073, places=6)
        self.assertAlmostEqual(errors["3034197250"], 0.003660, places=6)
        self.assertFalse(result["proj_warning_seen_with_proj_data"])


if __name__ == "__main__":
    unittest.main()
