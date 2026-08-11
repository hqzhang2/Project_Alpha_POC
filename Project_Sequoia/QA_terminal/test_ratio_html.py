#!/usr/bin/env python3
"""
Tests for the Ratio Analysis page (ratio.html) — static-file invariants.

Regression net for the 2026-08-10 change: the BUY/SELL/HOLD Signal column
was removed from the watchlist heatmap (Hong scope); the detail-view Signal
card (OVERSOLD/OVERBOUGHT/NEUTRAL) and the MACD signal line stay. These
assertions pin the 8-column shape so the column cannot silently reappear.
No network, no browser — pure file inspection.
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
HTML = open(os.path.join(HERE, "ratio.html"), encoding="utf-8").read()

EXPECTED_HEADERS = ["Ticker", "Price", "1D", "1W", "1M", "3M", "YTD", "1Y"]
FORBIDDEN = ["<th>Signal</th>", "signal-cell", "signal-tooltip", "sigColor",
             "sigReasons", "'BUY'", "'SELL'", "'HOLD'"]


def test_signal_column_removed():
    for token in FORBIDDEN:
        assert token not in HTML


def test_header_is_eight_columns():
    head = re.search(r"<thead>(.*?)</thead>", HTML, re.S)
    assert head is not None  # thead must exist
    headers = re.findall(r"<th[^>]*>([^<]*)</th>", head.group(1))
    assert headers == EXPECTED_HEADERS


def test_colspans_match_eight_columns():
    assert 'colspan="8"' in HTML  # loading placeholder
    assert 'colspan="7"' in HTML  # error placeholder (ticker + 7 cells)


def test_detail_signal_card_kept():
    assert 'id="signalValue"' in HTML
    assert "OVERSOLD" in HTML and "OVERBOUGHT" in HTML
    assert HTML.count('class="data-card"') == 4
    assert "repeat(4, 1fr)" in HTML


def test_macd_signal_line_kept():
    assert HTML.count("macd_signal") == 1
