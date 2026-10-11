import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from synoSNMP import snmpparse as P


class ParseTests(unittest.TestCase):
    def one(self, text):
        vbs, others = P.parse_output(text)
        self.assertEqual(len(vbs), 1, (vbs, others))
        return vbs[0]

    # --- formats captured from real net-snmp 5.9.4 output ---------------------------------
    def test_integer_string_gauge_counter64(self):
        self.assertEqual(self.one(".1.3.6.1.4.1.6574.1.1.0 = INTEGER: 1").value, 1)
        vb = self.one('.1.3.6.1.4.1.6574.1.5.1.0 = STRING: "DS920+"')
        self.assertEqual((vb.type, vb.value, vb.oid), (P.STRING, "DS920+", "1.3.6.1.4.1.6574.1.5.1.0"))
        self.assertEqual(self.one(".1.3.6.1.2.1.31.1.1.1.15.1 = Gauge32: 10").as_int(), 10)
        vb = self.one(".1.3.6.1.4.1.6574.101.1.1.12.1 = Counter64: 18446744073709551000")
        self.assertEqual((vb.type, vb.value), (P.COUNTER64, 18446744073709551000))

    def test_timeticks_with_and_without_raw_flag(self):
        self.assertEqual(self.one(".1.3.6.1.2.1.25.1.1.0 = Timeticks: (29222) 0:04:52.22").value, 29222)
        self.assertEqual(self.one(".1.3.6.1.2.1.1.3.0 = 877").value, 877)       # -Ot style

    def test_oid_value_and_bare_empty_string(self):
        self.assertEqual(self.one(".1.3.6.1.2.1.25.2.3.1.2.34 = OID: .1.3.6.1.2.1.25.2.1.4").value, "1.3.6.1.2.1.25.2.1.4")
        vb = self.one('.1.3.6.1.2.1.2.2.1.6.1 = ""')
        self.assertEqual((vb.type, vb.value), (P.STRING, ""))

    def test_missing_objects(self):
        for text in (".1.3.6.1.2.1.9.9.0 = No Such Instance currently exists at this OID",
                     ".1.3.6.1.2.1.9.9.0 = No Such Object available on this agent at this OID"):
            self.assertTrue(self.one(text).missing)
        self.assertEqual(self.one(".1.3.6.1.9 = No more variables left in this MIB View (It is past the end of the MIB tree)").type, P.ENDOFMIB)

    def test_enum_style_integer_and_units(self):
        self.assertEqual(self.one(".1.3.6.1.2.1.2.2.1.8.2 = INTEGER: up(1)").value, 1)
        self.assertEqual(self.one(".1.3.6.1.2.1.2.2.1.8.2 = INTEGER: 34 degrees C").value, 34)

    # --- opaque floats (Synology UPS MIB) ---------------------------------------------------
    def test_opaque_float_forms(self):
        self.assertAlmostEqual(self.one(".1.3.6.1.4.1.6574.4.3.1.1.0 = Opaque: Float: 100.000000").as_float(), 100.0)
        self.assertAlmostEqual(self.one(".1.3.6.1.4.1.6574.4.3.1.1.0 = Opaque: Double: 87.5").as_float(), 87.5)
        # raw BER float 100.0 = 9f 78 04 42 c8 00 00, as the old pysnmp code saw it ("0x9f78...")
        self.assertAlmostEqual(self.one(".1.3.6.1.4.1.6574.4.3.1.1.0 = OPAQUE: 9f 78 04 42 c8 00 00").as_float(), 100.0)
        self.assertAlmostEqual(self.one(".1.3.6.1.4.1.6574.4.3.1.1.0 = Hex-STRING: 9F 78 04 42 C8 00 00").as_float(), 100.0)
        self.assertIsNone(P.decode_opaque_float("de ad be ef"))

    def test_numeric_string_converts(self):
        self.assertEqual(self.one('.1.3.6.1.4.1.6574.4.3.6.1.0 = STRING: "3480"').as_int(), 3480)
        self.assertAlmostEqual(self.one('.1.3.6.1.4.1.6574.4.2.12.1.0 = STRING: "21.5"').as_float(), 21.5)

    # --- structure -------------------------------------------------------------------------------
    def test_multiline_string_continuation(self):
        vbs, others = P.parse_output('.1.3.6.1.2.1.1.1.0 = STRING: "Linux ds920\nsecond line"\n.1.3.6.1.2.1.1.5.0 = STRING: "ds"\n')
        self.assertEqual(len(vbs), 2)
        self.assertIn("second line", vbs[0].value)
        self.assertEqual(vbs[1].value, "ds")
        self.assertEqual(others, [])

    def test_errors_are_separated_from_values(self):
        text = ("Error in packet\nReason: (noSuchName) There is no such variable name in this MIB.\n"
                "Failed object: .1.3.6.1.4.1.6574.1.9.9.0\n\n.1.3.6.1.4.1.6574.1.1.0 = INTEGER: 1\n")
        vbs, others = P.parse_output(text)
        self.assertEqual([v.oid for v in vbs], ["1.3.6.1.4.1.6574.1.1.0"])
        self.assertEqual(len(others), 3)
        vbs, others = P.parse_output("Timeout: No Response from udp:10.0.0.1:161.\n")
        self.assertEqual((vbs, len(others)), ([], 1))

    def test_accessors_on_wrong_types_return_default(self):
        vb = self.one('.1.3.6.1.4.1.6574.1.5.1.0 = STRING: "DS920+"')
        self.assertIsNone(vb.as_int())
        self.assertEqual(vb.as_int(-1), -1)
        self.assertEqual(vb.as_text(), "DS920+")


if __name__ == "__main__":
    unittest.main()
