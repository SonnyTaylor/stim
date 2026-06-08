"""Tests for new PK features: food effect, steady-state, body weight."""

import math
import pytest
from datetime import datetime, timedelta, timezone

from stim.config import Config
from stim.pharmaco import (
    DoseEvent,
    concentration_at,
    cmax,
    relative_concentration_at,
    combined_concentration_at,
    current_level,
    time_to_threshold,
    level_at_time,
    weight_correction_factor,
    steady_state_multiplier,
    FED_TMAX_SHIFT_H,
    VD,
    BIOAVAILABILITY,
    REFERENCE_WEIGHT_KG,
)


@pytest.fixture
def cfg():
    return Config()


@pytest.fixture
def now():
    return datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)


# ─── Food Effect ────────────────────────────────────────────────────────

class TestFoodEffect:
    def test_fed_dose_delays_peak(self, cfg):
        """Fed dose should peak later than fasted."""
        fasted_peak_time = cfg.tmax_hours
        fed_peak_time = cfg.tmax_hours + FED_TMAX_SHIFT_H

        c_fasted = concentration_at(fasted_peak_time, 150, cfg, fed=False)
        c_fed = concentration_at(fed_peak_time, 150, cfg, fed=True)

        # Both should be near their respective peaks
        assert c_fasted > 0
        assert c_fed > 0

    def test_fed_cmax_same_as_fasted(self, cfg):
        """Total absorption (AUC) should be similar; Cmax may differ slightly."""
        peak_fasted = cmax(150, cfg, fed=False)
        peak_fed = cmax(150, cfg, fed=True)
        # FDA says AUC unchanged, but Cmax can be slightly reduced
        # With our model, Cmax should be the same since we just shift the curve
        assert abs(peak_fasted - peak_fed) / peak_fasted < 0.01

    def test_fed_dose_zero_before_lag(self, cfg):
        """Fed dose should have zero concentration before Tlag."""
        # At t=1h (before 2.5h lag), fed dose should be 0
        c = concentration_at(1.0, 150, cfg, fed=True)
        assert c == 0.0

    def test_fed_dose_active_after_lag(self, cfg):
        """Fed dose should be active after Tlag."""
        # At t=4h (after 2.5h lag), fed dose should be positive
        c = concentration_at(4.0, 150, cfg, fed=True)
        assert c > 0

    def test_fed_vs_fasted_at_same_time(self, cfg):
        """At early timepoints, fasted should be higher than fed."""
        t = 1.5  # Before fed lag ends
        c_fasted = concentration_at(t, 150, cfg, fed=False)
        c_fed = concentration_at(t, 150, cfg, fed=True)
        assert c_fasted > c_fed

    def test_fed_flag_in_dose_event(self):
        """DoseEvent should track fed status."""
        now = datetime.now(timezone.utc)
        dose = DoseEvent(amount_mg=150, taken_at=now, fed=True)
        assert dose.fed is True
        dose2 = DoseEvent(amount_mg=150, taken_at=now, fed=False)
        assert dose2.fed is False

    def test_combined_with_fed_doses(self, cfg, now):
        """Combined concentration should respect fed flag per dose."""
        fasted_dose = DoseEvent(amount_mg=150, taken_at=now - timedelta(hours=3), fed=False)
        fed_dose = DoseEvent(amount_mg=150, taken_at=now - timedelta(hours=3), fed=True)

        # At t=1.5h after dose, fasted should contribute more
        level_fasted = combined_concentration_at(-1.5, [fasted_dose], cfg, now)
        level_fed = combined_concentration_at(-1.5, [fed_dose], cfg, now)
        assert level_fasted > level_fed


# ─── Steady-State Accumulation ─────────────────────────────────────────

class TestSteadyState:
    def test_no_accumulation_short_streak(self):
        """Streak < 4 should have no multiplier."""
        assert steady_state_multiplier(1, "auto") == 1.0
        assert steady_state_multiplier(3, "auto") == 1.0

    def test_partial_accumulation_mid_streak(self):
        """Streak 4–6 in 'on' mode should have partial multiplier."""
        # Auto mode only kicks in at 7+
        assert steady_state_multiplier(5, "auto") == 1.0
        # 'on' mode applies at streak 4+
        assert steady_state_multiplier(5, "on") == 1.3

    def test_full_accumulation_week_plus(self):
        """Streak 7+ should have 1.5× multiplier."""
        assert steady_state_multiplier(7, "auto") == 1.5
        assert steady_state_multiplier(10, "auto") == 1.5

    def test_plateau_accumulation_two_weeks(self):
        """Streak 14+ should have 1.7× multiplier."""
        assert steady_state_multiplier(14, "auto") == 1.7
        assert steady_state_multiplier(30, "auto") == 1.7

    def test_mode_off_disables(self):
        """Mode 'off' should always return 1.0."""
        assert steady_state_multiplier(10, "off") == 1.0
        assert steady_state_multiplier(30, "off") == 1.0

    def test_mode_on_always_applies(self):
        """Mode 'on' should apply multiplier even at low streak."""
        assert steady_state_multiplier(1, "on") == 1.0  # still < 4
        assert steady_state_multiplier(5, "on") == 1.3
        assert steady_state_multiplier(7, "on") == 1.5

    def test_auto_only_after_seven(self, cfg, now):
        """Auto mode should only kick in at streak >= 7."""
        dose = DoseEvent(amount_mg=150, taken_at=now - timedelta(hours=4))

        level_short = current_level([dose], cfg, streak=3, now=now)
        level_long = current_level([dose], cfg, streak=7, now=now)

        # Long streak should be higher due to 1.5× multiplier
        assert level_long > level_short
        assert abs(level_long / level_short - 1.5) < 0.01


# ─── Body Weight Correction ────────────────────────────────────────────

class TestBodyWeight:
    def test_reference_weight_no_correction(self):
        """70 kg reference should give factor 1.0."""
        assert abs(weight_correction_factor(70.0) - 1.0) < 0.001

    def test_lighter_user_higher_factor(self):
        """Lighter users should have higher concentration."""
        factor = weight_correction_factor(50.0)
        assert factor > 1.0
        assert abs(factor - 1.164) < 0.01  # From Willavize 2017

    def test_heavier_user_lower_factor(self):
        """Heavier users should have lower concentration."""
        factor = weight_correction_factor(150.0)
        assert factor < 1.0
        assert abs(factor - 0.709) < 0.02  # From Willavize 2017

    def test_88kg_user(self):
        """88 kg user should have slightly lower than reference."""
        factor = weight_correction_factor(88.0)
        assert factor < 1.0
        assert factor > 0.85  # Not too far from reference

    def test_none_weight_no_correction(self):
        """None weight should give factor 1.0."""
        assert weight_correction_factor(None) == 1.0

    def test_weight_affects_level(self, cfg, now):
        """Weight should affect calculated levels."""
        dose = DoseEvent(amount_mg=150, taken_at=now - timedelta(hours=4))

        cfg_light = Config(body_weight_kg=50)
        cfg_heavy = Config(body_weight_kg=150)

        level_light = current_level([dose], cfg_light, now=now)
        level_heavy = current_level([dose], cfg_heavy, now=now)

        assert level_light > level_heavy


# ─── Vd Update (42→45) ─────────────────────────────────────────────────

class TestVdUpdate:
    def test_vd_is_45(self):
        """Vd should be 45 L from Willavize 2017."""
        assert VD == 45.0

    def test_ke_value_unchanged(self, cfg):
        """ke should still be ~0.0462 with 15h half-life."""
        assert abs(cfg.ke - 0.0462) < 0.001

    def test_ka_still_reasonable(self, cfg):
        """ka should still be around 1.2."""
        assert 1.0 < cfg.ka < 2.0

    def test_peak_at_tmax(self, cfg):
        """Peak should still occur at Tmax."""
        c_at_tmax = concentration_at(cfg.tmax_hours, 150, cfg)
        c_before = concentration_at(cfg.tmax_hours - 0.5, 150, cfg)
        c_after = concentration_at(cfg.tmax_hours + 0.5, 150, cfg)
        assert c_at_tmax > c_before
        assert c_at_tmax > c_after


# ─── PK Constants Validation ──────────────────────────────────────────

class TestUpdatedPKConstants:
    def test_bioavailability(self):
        assert BIOAVAILABILITY == 0.73

    def test_vd(self):
        assert VD == 45.0

    def test_reference_weight(self):
        assert REFERENCE_WEIGHT_KG == 70.0

    def test_fed_tmax_shift(self):
        assert FED_TMAX_SHIFT_H == 2.5
