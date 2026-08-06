"""
Unit tests for sp500_history.py membership reconstruction (network-free).

members_on walks the changes table backward from the current list; the
fetch/parse side (network) is exercised live via the CLI, not here.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sp500_history as sh

DATA = {
    "current": ["B", "C", "E", "F"],     # end state: A/D swapped/removed out
    "changes": [
        ["2017-01-01", None, "D"],      # D removed 2017
        ["2019-01-01", "E", None],      # E added 2019
        ["2021-01-01", "F", "A"],       # F in, A out (swap) 2021
    ],
}


class TestMembersOn(unittest.TestCase):
    def test_after_removal_before_addition(self):
        m = sh.members_on("2018-06-01", DATA)
        self.assertEqual(m, {"A", "B", "C"})       # D out, E/F not yet in

    def test_after_all_changes_matches_current(self):
        m = sh.members_on("2026-01-01", DATA)
        self.assertEqual(m, {"B", "C", "E", "F"})  # == current, A/D swapped out

    def test_before_any_change_matches_all_time_members(self):
        m = sh.members_on("2016-01-01", DATA)
        self.assertEqual(m, {"A", "B", "C", "D"})  # nothing changed yet

    def test_swap_is_atomic_per_date(self):
        # A change row with added+removed applies both at once (same date)
        m = sh.members_on("2021-06-01", DATA)
        self.assertIn("F", m)
        self.assertNotIn("A", m)


if __name__ == "__main__":
    unittest.main(verbosity=2)
