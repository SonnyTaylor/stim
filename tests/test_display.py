"""Tests for display helpers."""

import pytest
from datetime import datetime, timezone

from stim.config import Config
from stim.display import (
    color_for_level,
    color_for_streak,
    level_bar,
    sparkline,
    format_local_time,
    format_local_datetime,
    parse_mg,
)


@pytest.fixture
def cfg():
    return Config()


# ─── Color Helpers ─────────────────────────────────────────────────────

class TestColorForLevel:
    def test_green_below_15(self, cfg):
        assert color_for_level(5.0, cfg) == "green"
        assert color_for_level(14.9, cfg) == "green"

    def test_yellow_between_thresholds(self, cfg):
        # medium threshold is 25%
        assert color_for_level(15.0, cfg) == "yellow"
        assert color_for_level(24.9, cfg) == "yellow"

    def test_red_above_threshold(self, cfg):
        assert color_for_level(25.0, cfg) == "red"
        assert color_for_level(50.0, cfg) == "red"

    def test_low_sensitivity(self):
        cfg = Config(sleep_sensitivity="low")
        assert color_for_level(30.0, cfg) == "yellow"
        assert color_for_level(35.1, cfg) == "red"

    def test_high_sensitivity(self):
        cfg = Config(sleep_sensitivity="high")
        assert color_for_level(14.9, cfg) == "green"
        # 15% threshold means 15.1 is above it → red
        assert color_for_level(15.1, cfg) == "red"
        # Between 15% and 35% (low threshold) with high sensitivity → yellow is not used
        # because high threshold = 15%, so anything above 15% is red


class TestColorForStreak:
    def test_green_short_streak(self, cfg):
        assert color_for_streak(0, cfg) == "green"
        assert color_for_streak(1, cfg) == "green"
        assert color_for_streak(2, cfg) == "green"

    def test_yellow_at_warn(self, cfg):
        assert color_for_streak(3, cfg) == "yellow"
        assert color_for_streak(4, cfg) == "yellow"

    def test_red_at_alert(self, cfg):
        assert color_for_streak(5, cfg) == "bold red"
        assert color_for_streak(10, cfg) == "bold red"


# ─── Level Bar ─────────────────────────────────────────────────────────

class TestLevelBar:
    def test_empty_at_zero(self):
        bar = level_bar(0, width=10)
        assert bar == "░" * 10

    def test_full_at_100(self):
        bar = level_bar(100, width=10)
        assert bar == "█" * 10

    def test_half(self):
        bar = level_bar(50, width=10)
        assert bar == "█████░░░░░"

    def test_custom_width(self):
        bar = level_bar(25, width=20)
        assert len(bar) == 20
        assert bar.count("█") == 5
        assert bar.count("░") == 15

    def test_clamped_values(self):
        # Negative should be empty
        bar = level_bar(-10, width=10)
        assert bar == "░" * 10
        # Over 100 should be full
        bar = level_bar(150, width=10)
        assert bar == "█" * 10


# ─── Sparkline ─────────────────────────────────────────────────────────

class TestSparkline:
    def test_empty(self):
        assert sparkline([]) == ""

    def test_single_value(self):
        result = sparkline([5.0])
        assert len(result) == 1

    def test_constant_values(self):
        # All same value should give same character
        result = sparkline([5.0, 5.0, 5.0])
        assert len(set(result)) == 1

    def test_increasing(self):
        result = sparkline([1, 2, 3, 4, 5])
        # Characters should be ascending blocks
        assert len(result) == 5

    def test_width_sampling(self):
        values = list(range(100))
        result = sparkline(values, width=10)
        assert len(result) == 10


# ─── Time Formatting ───────────────────────────────────────────────────

class TestTimeFormatting:
    def test_format_local_time(self):
        utc_str = "2026-06-09T12:00:00Z"
        result = format_local_time(utc_str)
        # Result depends on local timezone, just check format
        assert ":" in result
        assert len(result) == 5  # HH:MM

    def test_format_local_datetime(self):
        utc_str = "2026-06-09T12:00:00Z"
        result = format_local_datetime(utc_str)
        assert "2026" in result or "2025" in result  # depending on timezone
        assert ":" in result

    def test_format_local_time_custom_format(self):
        utc_str = "2026-06-09T12:00:00Z"
        result = format_local_time(utc_str, fmt="%I:%M %p")
        # Should have AM/PM
        assert "AM" in result or "PM" in result or "am" in result or "pm" in result


# ─── Dose Parsing ──────────────────────────────────────────────────────

class TestParseMg:
    def test_with_mg_suffix(self):
        assert parse_mg("150mg") == 150.0

    def test_without_suffix(self):
        assert parse_mg("150") == 150.0

    def test_with_spaces(self):
        assert parse_mg(" 150mg ") == 150.0

    def test_decimal(self):
        assert parse_mg("75.5mg") == 75.5

    def test_case_insensitive(self):
        assert parse_mg("150MG") == 150.0
        assert parse_mg("150Mg") == 150.0

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_mg("abc")
