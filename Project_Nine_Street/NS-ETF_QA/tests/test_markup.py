"""Dashboard markup-invariant tests (static HTML served fresh per request —
pytest is the only thing that can see it; NS-7 lesson)."""
import re
import unittest
from pathlib import Path

DASH = Path(__file__).resolve().parent.parent / "nsetf_dashboard.html"


class TestMarkup(unittest.TestCase):
    def setUp(self):
        self.html = DASH.read_text()

    def test_perf_chart_canvas(self):
        self.assertIn('id="perfChart"', self.html)

    def test_no_duplicate_let(self):
        lets = re.findall(r"\blet\s+", self.html)
        decls = re.findall(r"^\s*let\s+(\w+)", self.html, re.M)
        self.assertEqual(len(decls), len(set(decls)), f"duplicate let: {decls}")

    def test_politeness_outperform_not_beat(self):
        self.assertNotIn("beat", self.html.lower())

    def test_advisory_badged(self):
        self.assertIn("ADVISORY ONLY", self.html)
        self.assertIn("zero allocation impact", self.html)

    def test_error_rows_skipped_in_advisory(self):
        # regression guard: NS-4 dashboard crashed on error rows
        self.assertIn("filter(r => !r.error)", self.html)

    def test_vix_spot_and_avg_present(self):
        self.assertIn("VIX spot", self.html)
        self.assertIn("avg", self.html)

    def test_vix_on_right_axis(self):
        # PM spec rev 2: LEFT = growth of $100, RIGHT = VIX
        self.assertIn("side: 'left'", self.html)
        self.assertIn("Growth of $100", self.html)
        self.assertIn("VIX spot (right)", self.html)
        self.assertIn("VIX SMA (right)", self.html)

    def test_metrics_and_scores_panels(self):
        self.assertIn('id="metricsRow"', self.html)
        self.assertIn('id="scoreTable"', self.html)
        self.assertIn('id="scoreLeaders"', self.html)
        self.assertIn("composite_scores", self.html)
        self.assertIn("Accept advisory", self.html)
        self.assertIn("/api/advisory/accept", self.html)

    def test_crosshair_present(self):
        self.assertIn("plotly_hover", self.html)


if __name__ == "__main__":
    unittest.main()
