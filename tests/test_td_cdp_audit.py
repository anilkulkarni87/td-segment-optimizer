"""Unit tests for td_cdp_audit.py using synthetic fixtures. Run: python3 -m unittest discover tests"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import td_cdp_audit as t  # noqa: E402

FX = os.path.join(HERE, "fixtures")


def load(name):
    with open(os.path.join(FX, name)) as f:
        return json.load(f)


class AnalyzeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = t.analyze(load("audience.json"), load("segments.json"), load("activations.json"),
                            load("customers_schema.json"), recent_days=180, today=dt.date(2026, 10, 1))
        cls.rows = {(r["kind"], r["column"]): r for r in cls.res["rows"]}

    def status(self, kind, col):
        return self.rows[(kind, col)]["status"]

    def test_attribute_statuses(self):
        self.assertEqual(self.status("attribute", "country"), "active")
        self.assertEqual(self.status("attribute", "email_opt_in"), "active")
        self.assertEqual(self.status("attribute", "loyalty_tier"), "dormant")  # only 2024 segment
        self.assertEqual(self.status("attribute", "first_name"), "activation_only")
        for col in ("phone", "last_purchase_at", "hiking_fan"):
            self.assertEqual(self.status("attribute", col), "never_used", col)

    def test_activation_counts(self):
        self.assertEqual(self.rows[("attribute", "country")]["activations"], 1)

    def test_behaviors(self):
        self.assertEqual(self.status("behavior", "behavior_order_events"), "active")
        self.assertEqual(self.status("behavior", "behavior_pageviews"), "never_used")
        self.assertEqual(self.status("behavior_column", "category"), "active")
        self.assertEqual(self.status("behavior_column", "amount"), "active")  # aggregation column
        self.assertEqual(self.status("behavior_column", "sku"), "never_used")

    def test_unmapped_fields_classified(self):
        u = {x["field"]: x["classification"] for x in self.res["unmapped_rule_fields"]}
        self.assertEqual(u["known_profile"], "matrix_column")
        self.assertEqual(u["legacy_region"], "unknown_or_removed")

    def test_broken_reference(self):
        self.assertEqual([x["referenced_segment"] for x in self.res["broken_references"]], ["9999"])

    def test_summary(self):
        s = self.res["summary"]
        self.assertEqual(s["segments_total"], 4)
        self.assertEqual(s["segments_with_rules"], 3)
        self.assertEqual(s["counts"]["attribute"]["total"], 7)
        self.assertEqual(s["counts"]["attribute"]["never_used"], 3)

    def test_markdown_renders(self):
        md = t.render_markdown(self.res)
        self.assertIn("Never-used attributes by group", md)
        self.assertIn("`legacy_region`", md)


class DeltaTest(unittest.TestCase):
    def test_compute_delta_detects_newly_adopted_attributes(self):
        aud = load("audience.json")
        segs = load("segments.json")
        # In segments.json:
        # segment 1001 was created 2026-01-10 (uses country, email_opt_in)
        # segment 1002 was created 2024-02-01 (uses loyalty_tier)
        # segment 1003 was created 2026-08-01 (uses known_profile, legacy_region)
        # Add a new segment created 2026-09-15 using 'hiking_fan' (previously never used)
        new_seg = {
            "id": "1005", "name": "Hikers Club", "createdAt": "2026-09-15T00:00:00Z", "updatedAt": "2026-09-15T00:00:00Z",
            "rule": {"type": "And", "conditions": [
                {"type": "Value", "leftValue": {"name": "hiking_fan"}, "operator": {"type": "Equal", "rightValue": "true", "not": False}, "exclude": False}
            ]}
        }
        test_segs = segs + [new_seg]
        delta = t.compute_delta(test_segs, aud, since="2026-09-01")
        self.assertEqual(delta["segments_created_count"], 2)  # 1004 (empty draft 2026-09-30) + 1005
        adopted = [a["column"] for a in delta["newly_adopted_attributes"]]
        self.assertIn("hiking_fan", adopted)
        self.assertNotIn("country", adopted)  # country was already used prior to 2026-09-01

    def test_delta_rendered_in_markdown(self):
        aud = load("audience.json")
        segs = load("segments.json")
        res = t.analyze(aud, segs, [], [], 180, since="2026-07-01")
        md = t.render_markdown(res)
        self.assertIn("## Incremental Activity (Since 2026-07-01)", md)


class EndpointTest(unittest.TestCase):
    def test_cdp_from_api(self):
        self.assertEqual(t._cdp_from_api("https://api.eu01.treasuredata.com"),
                         "https://api-cdp.eu01.treasuredata.com")
        self.assertEqual(t._cdp_from_api("api.treasuredata.co.jp"), "https://api-cdp.treasuredata.co.jp")
        self.assertEqual(t._cdp_from_api("https://api-cdp.treasuredata.com"), "https://api-cdp.treasuredata.com")

    def test_api_from_cdp(self):
        self.assertEqual(t._api_from_cdp("https://api-cdp.ap02.treasuredata.com"),
                         "https://api.ap02.treasuredata.com")


class CliOfflineTest(unittest.TestCase):
    def test_analyze_command_writes_outputs(self):
        d = tempfile.mkdtemp()
        try:
            for f in os.listdir(FX):
                shutil.copy(os.path.join(FX, f), d)
            t.main(["analyze", "-i", d, "--since", "2026-01-01"])
            for f in ("report.md", "attribute_usage.csv", "summary.json"):
                self.assertTrue(os.path.exists(os.path.join(d, f)), f)
            with open(os.path.join(d, "report.md")) as f:
                content = f.read()
                self.assertIn("Incremental Activity", content)
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
