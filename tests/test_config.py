"""Tests for config management."""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from stim.config import Config, SENSITIVITY_THRESHOLDS


class TestConfigDefaults:
    def test_default_dose(self):
        cfg = Config()
        assert cfg.default_dose_mg == 150.0

    def test_default_half_life(self):
        cfg = Config()
        assert cfg.half_life_hours == 15.0

    def test_default_tmax(self):
        cfg = Config()
        assert cfg.tmax_hours == 2.0

    def test_default_sleep_time(self):
        cfg = Config()
        assert cfg.sleep_time == "22:00"

    def test_default_sensitivity(self):
        cfg = Config()
        assert cfg.sleep_sensitivity == "medium"

    def test_default_late_cutoff(self):
        cfg = Config()
        assert cfg.late_dose_cutoff == "13:00"

    def test_default_streak_warn(self):
        cfg = Config()
        assert cfg.streak_warn_days == 3

    def test_default_streak_alert(self):
        cfg = Config()
        assert cfg.streak_alert_days == 5


class TestConfigDerived:
    def test_ke_from_half_life(self):
        import math
        cfg = Config(half_life_hours=15.0)
        expected = math.log(2) / 15.0
        assert abs(cfg.ke - expected) < 1e-10

    def test_ke_different_half_life(self):
        import math
        cfg = Config(half_life_hours=10.0)
        expected = math.log(2) / 10.0
        assert abs(cfg.ke - expected) < 1e-10

    def test_ka_from_tmax(self):
        cfg = Config()
        # ka should satisfy: ka * exp(-ka * tmax) = ke * exp(-ke * tmax)
        import math
        ka, ke, tmax = cfg.ka, cfg.ke, cfg.tmax_hours
        lhs = ka * math.exp(-ka * tmax)
        rhs = ke * math.exp(-ke * tmax)
        assert abs(lhs - rhs) < 1e-6

    def test_sleep_threshold_values(self):
        assert Config(sleep_sensitivity="low").sleep_threshold == 0.35
        assert Config(sleep_sensitivity="medium").sleep_threshold == 0.25
        assert Config(sleep_sensitivity="high").sleep_threshold == 0.15


class TestSensitivityThresholds:
    def test_all_sensitivities_defined(self):
        assert "low" in SENSITIVITY_THRESHOLDS
        assert "medium" in SENSITIVITY_THRESHOLDS
        assert "high" in SENSITIVITY_THRESHOLDS

    def test_threshold_ordering(self):
        assert SENSITIVITY_THRESHOLDS["high"] < SENSITIVITY_THRESHOLDS["medium"]
        assert SENSITIVITY_THRESHOLDS["medium"] < SENSITIVITY_THRESHOLDS["low"]


class TestConfigCustomValues:
    def test_custom_dose(self):
        cfg = Config(default_dose_mg=75.0)
        assert cfg.default_dose_mg == 75.0

    def test_custom_half_life(self):
        cfg = Config(half_life_hours=12.0)
        assert cfg.half_life_hours == 12.0

    def test_custom_sensitivity(self):
        cfg = Config(sleep_sensitivity="high")
        assert cfg.sleep_threshold == 0.15
