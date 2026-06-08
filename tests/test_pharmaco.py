"""Tests for pharmacokinetic calculations."""

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
    curve_points,
    clearance_time,
)


# ─── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def cfg():
    """Default config for testing."""
    return Config(
        default_dose_mg=150.0,
        half_life_hours=15.0,
        tmax_hours=2.0,
        sleep_time="22:00",
        sleep_sensitivity="medium",
        late_dose_cutoff="13:00",
    )


@pytest.fixture
def now():
    """Fixed reference time for reproducible tests."""
    return datetime(2026, 6, 9, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def single_dose(now):
    """A single 150mg dose taken 4 hours ago."""
    return [DoseEvent(amount_mg=150.0, taken_at=now - timedelta(hours=4))]


@pytest.fixture
def two_doses(now):
    """Two doses: 150mg 8h ago and 75mg 2h ago."""
    return [
        DoseEvent(amount_mg=150.0, taken_at=now - timedelta(hours=8)),
        DoseEvent(amount_mg=75.0, taken_at=now - timedelta(hours=2)),
    ]


# ─── Config Tests ──────────────────────────────────────────────────────

class TestConfig:
    def test_default_values(self):
        cfg = Config()
        assert cfg.default_dose_mg == 150.0
        assert cfg.half_life_hours == 15.0
        assert cfg.tmax_hours == 2.0
        assert cfg.sleep_time == "22:00"
        assert cfg.sleep_sensitivity == "medium"

    def test_ke_calculation(self, cfg):
        """ke = ln(2) / t½"""
        expected_ke = math.log(2) / 15.0
        assert abs(cfg.ke - expected_ke) < 1e-6

    def test_ka_calculation(self, cfg):
        """ka should be derived from Tmax and ke."""
        # ka should be larger than ke (absorption faster than elimination)
        assert cfg.ka > cfg.ke
        # Verify Tmax formula: Tmax = ln(ka/ke) / (ka - ke)
        ka, ke = cfg.ka, cfg.ke
        tmax_calc = math.log(ka / ke) / (ka - ke)
        assert abs(tmax_calc - cfg.tmax_hours) < 0.01

    def test_ke_with_different_half_life(self):
        cfg = Config(half_life_hours=10.0)
        expected_ke = math.log(2) / 10.0
        assert abs(cfg.ke - expected_ke) < 1e-6

    def test_sleep_threshold(self):
        assert Config(sleep_sensitivity="low").sleep_threshold == 0.35
        assert Config(sleep_sensitivity="medium").sleep_threshold == 0.25
        assert Config(sleep_sensitivity="high").sleep_threshold == 0.15


# ─── Single Dose Concentration Tests ──────────────────────────────────

class TestConcentrationAt:
    def test_t_zero_is_zero(self, cfg):
        """At t=0, concentration should be 0 (just took the dose)."""
        assert concentration_at(0, 150, cfg) == 0.0

    def test_negative_time_is_zero(self, cfg):
        """Before dose, concentration is 0."""
        assert concentration_at(-1, 150, cfg) == 0.0

    def test_peak_at_tmax(self, cfg):
        """Maximum concentration should occur at Tmax."""
        c_at_tmax = concentration_at(cfg.tmax_hours, 150, cfg)
        c_before = concentration_at(cfg.tmax_hours - 0.5, 150, cfg)
        c_after = concentration_at(cfg.tmax_hours + 0.5, 150, cfg)
        assert c_at_tmax > c_before
        assert c_at_tmax > c_after

    def test_concentration_positive_during_active(self, cfg):
        """Concentration should be positive between 0 and clearance."""
        for t in [0.5, 1, 2, 4, 8, 12, 24]:
            assert concentration_at(t, 150, cfg) > 0

    def test_concentration_decreases_after_peak(self, cfg):
        """After Tmax, concentration should monotonically decrease."""
        peak_val = concentration_at(cfg.tmax_hours, 150, cfg)
        for hours_after in [1, 2, 4, 8, 12, 24]:
            t = cfg.tmax_hours + hours_after
            val = concentration_at(t, 150, cfg)
            assert val < peak_val
            assert val > 0  # still positive

    def test_concentration_approaches_zero(self, cfg):
        """After many half-lives, concentration should be very small."""
        # After 7 half-lives, should be < 1% of peak
        t = cfg.tmax_hours + cfg.half_life_hours * 7
        val = concentration_at(t, 150, cfg)
        peak = cmax(150, cfg)
        assert val / peak < 0.01

    def test_proportional_to_dose(self, cfg):
        """Concentration should be proportional to dose."""
        c_75 = concentration_at(4, 75, cfg)
        c_150 = concentration_at(4, 150, cfg)
        c_300 = concentration_at(4, 300, cfg)
        assert abs(c_150 / c_75 - 2.0) < 0.01
        assert abs(c_300 / c_150 - 2.0) < 0.01

    def test_zero_dose(self, cfg):
        """Zero dose should give zero concentration."""
        assert concentration_at(2, 0, cfg) == 0.0


class TestCmax:
    def test_cmax_positive(self, cfg):
        assert cmax(150, cfg) > 0

    def test_cmax_proportional_to_dose(self, cfg):
        assert abs(cmax(300, cfg) / cmax(150, cfg) - 2.0) < 0.01

    def test_cmax_at_tmax(self, cfg):
        """cmax should equal concentration_at(tmax)."""
        expected = concentration_at(cfg.tmax_hours, 150, cfg)
        assert abs(cmax(150, cfg) - expected) < 1e-10


class TestRelativeConcentration:
    def test_at_tmax_is_100(self, cfg):
        """At Tmax, relative concentration should be 100%."""
        rel = relative_concentration_at(cfg.tmax_hours, 150, cfg)
        assert abs(rel - 100.0) < 0.01

    def test_at_zero_is_zero(self, cfg):
        assert relative_concentration_at(0, 150, cfg) == 0.0

    def test_always_le_100(self, cfg):
        """Relative concentration should never exceed 100%."""
        for t in [0, 0.5, 1, 2, 3, 4, 8, 12, 24, 48]:
            rel = relative_concentration_at(t, 150, cfg)
            assert rel <= 100.0 + 0.01  # small tolerance for float

    def test_decreases_after_peak(self, cfg):
        """After peak, relative concentration should decrease."""
        peak = relative_concentration_at(cfg.tmax_hours, 150, cfg)
        later = relative_concentration_at(cfg.tmax_hours + 10, 150, cfg)
        assert peak > later

    def test_independent_of_dose_amount(self, cfg):
        """Relative concentration (%) should be the same regardless of dose."""
        rel_75 = relative_concentration_at(4, 75, cfg)
        rel_150 = relative_concentration_at(4, 150, cfg)
        rel_300 = relative_concentration_at(4, 300, cfg)
        assert abs(rel_75 - rel_150) < 0.01
        assert abs(rel_150 - rel_300) < 0.01


# ─── Multi-Dose Superposition Tests ───────────────────────────────────

class TestCombinedConcentration:
    def test_single_dose_matches_single(self, cfg, now, single_dose):
        """Combined with one dose should match single-dose calculation."""
        combined = combined_concentration_at(0, single_dose, cfg, now)
        # Manually calculate expected
        dose = single_dose[0]
        hours_since = 4.0  # dose was 4h ago
        abs_conc = concentration_at(hours_since, dose.amount_mg, cfg)
        ref_peak = cmax(cfg.default_dose_mg, cfg)
        expected = abs_conc / ref_peak * 100
        assert abs(combined - expected) < 0.01

    def test_two_doses_sum(self, cfg, now, two_doses):
        """Combined should be sum of individual contributions."""
        combined = combined_concentration_at(0, two_doses, cfg, now)
        ref_peak = cmax(cfg.default_dose_mg, cfg)

        # Individual contributions
        total = 0
        for d in two_doses:
            hours_since = (now - d.taken_at).total_seconds() / 3600.0
            total += concentration_at(hours_since, d.amount_mg, cfg)
        expected = total / ref_peak * 100

        assert abs(combined - expected) < 0.01

    def test_empty_doses_is_zero(self, cfg, now):
        assert combined_concentration_at(0, [], cfg, now) == 0.0

    def test_future_level_higher_with_more_doses(self, cfg, now):
        """Adding a dose should increase future levels."""
        one_dose = [DoseEvent(amount_mg=150, taken_at=now - timedelta(hours=2))]
        two_doses = one_dose + [DoseEvent(amount_mg=75, taken_at=now)]

        level_one = combined_concentration_at(2, one_dose, cfg, now)
        level_two = combined_concentration_at(2, two_doses, cfg, now)
        assert level_two > level_one


# ─── Time-to-Threshold Tests ──────────────────────────────────────────

class TestTimeToThreshold:
    def test_already_below(self, cfg, now):
        """If already below threshold, return now."""
        empty_doses = []
        result = time_to_threshold(empty_doses, 25.0, cfg, now)
        assert result == now

    def test_crosses_threshold(self, cfg, now):
        """Should find when level drops below threshold."""
        # Dose taken 2h ago so we're past absorption
        doses = [DoseEvent(amount_mg=150, taken_at=now - timedelta(hours=2))]
        result = time_to_threshold(doses, 10.0, cfg, now)
        assert result is not None
        assert result > now
        # Should be many hours later (armodafinil has long half-life)
        hours = (result - now).total_seconds() / 3600.0
        assert hours > 10  # at least 10 hours for 150mg to drop below 10%

    def test_higher_threshold_sooner(self, cfg, now):
        """Higher threshold should be reached sooner."""
        doses = [DoseEvent(amount_mg=150, taken_at=now - timedelta(hours=2))]
        time_low = time_to_threshold(doses, 10.0, cfg, now)
        time_high = time_to_threshold(doses, 25.0, cfg, now)
        assert time_high < time_low


# ─── Level-at-Time Tests ──────────────────────────────────────────────

class TestLevelAtTime:
    def test_at_dose_time(self, cfg, now):
        """Level at the exact time of dose should be ~0."""
        dose_time = now - timedelta(hours=1)
        doses = [DoseEvent(amount_mg=150, taken_at=dose_time)]
        # At dose_time + a tiny bit, level should be near 0
        level = level_at_time(dose_time + timedelta(seconds=1), doses, cfg)
        assert level < 1.0

    def test_at_peak_time(self, cfg, now):
        """Level at Tmax after dose should be near peak."""
        dose_time = now - timedelta(hours=cfg.tmax_hours)
        doses = [DoseEvent(amount_mg=150, taken_at=dose_time)]
        level = level_at_time(now, doses, cfg)
        # Should be close to 100% (the reference peak)
        assert level > 80  # reasonable threshold


# ─── Curve Points Tests ────────────────────────────────────────────────

class TestCurvePoints:
    def test_returns_lists(self, cfg, now, single_dose):
        xs, ys = curve_points(single_dose, -10, 10, 1.0, cfg, now)
        assert len(xs) == len(ys)
        assert len(xs) > 0

    def test_covers_range(self, cfg, now, single_dose):
        xs, ys = curve_points(single_dose, -5, 5, 1.0, cfg, now)
        assert xs[0] == -5
        assert xs[-1] == 5

    def test_step_size(self, cfg, now, single_dose):
        xs, ys = curve_points(single_dose, 0, 10, 0.5, cfg, now)
        for i in range(1, len(xs)):
            assert abs(xs[i] - xs[i-1] - 0.5) < 1e-10


# ─── Clearance Time Tests ─────────────────────────────────────────────

class TestClearanceTime:
    def test_empty_doses(self, cfg, now):
        """No doses means already clear."""
        result = clearance_time([], 10.0, cfg, now)
        assert result == now

    def test_returns_future_time(self, cfg, now):
        """Clearance should be in the future for recent dose."""
        doses = [DoseEvent(amount_mg=150, taken_at=now - timedelta(hours=2))]
        result = clearance_time(doses, 10.0, cfg, now)
        assert result is not None
        assert result > now


# ─── Edge Cases ────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_very_small_dose(self, cfg):
        """Very small dose should produce very small concentration."""
        c = concentration_at(2, 0.001, cfg)
        assert c > 0
        assert c < 0.001

    def test_very_large_dose(self, cfg):
        """Large dose should produce proportionally large concentration."""
        c_150 = cmax(150, cfg)
        c_1500 = cmax(1500, cfg)
        assert abs(c_1500 / c_150 - 10.0) < 0.01

    def test_long_time_after_dose(self, cfg):
        """After 100 hours, concentration should be very small."""
        c = concentration_at(100, 150, cfg)
        peak = cmax(150, cfg)
        assert c / peak < 0.02  # ~1% after 100h with 15h half-life

    def test_different_half_lives(self):
        """Faster half-life should clear faster."""
        cfg_fast = Config(half_life_hours=8.0)
        cfg_slow = Config(half_life_hours=20.0)

        c_fast = relative_concentration_at(24, 150, cfg_fast)
        c_slow = relative_concentration_at(24, 150, cfg_slow)

        assert c_fast < c_slow

    def test_superposition_with_many_doses(self, cfg, now):
        """Multiple doses should superpose correctly."""
        doses = [
            DoseEvent(amount_mg=100, taken_at=now - timedelta(hours=h))
            for h in range(10)
        ]
        level = combined_concentration_at(0, doses, cfg, now)
        assert level > 0


# ─── Pharmacokinetic Constants Validation ──────────────────────────────

class TestPKConstants:
    def test_ke_value(self, cfg):
        """ke for 15h half-life should be ~0.0462."""
        assert abs(cfg.ke - 0.0462) < 0.001

    def test_ka_value(self, cfg):
        """ka should be around 1.2 for Tmax=2h."""
        assert 1.0 < cfg.ka < 2.0

    def test_tmax_consistency(self, cfg):
        """Tmax calculated from ka, ke should match configured tmax."""
        tmax_calc = math.log(cfg.ka / cfg.ke) / (cfg.ka - cfg.ke)
        assert abs(tmax_calc - cfg.tmax_hours) < 0.01

    def test_bioavailability_used(self, cfg):
        """F=0.73 and Vd=45 should be used in concentration calculations."""
        F = 0.73
        Vd = 45.0  # Willavize 2017
        ka, ke = cfg.ka, cfg.ke
        expected_cmax = (F * 150 * ka) / (Vd * (ka - ke)) * (
            math.exp(-ke * cfg.tmax_hours) - math.exp(-ka * cfg.tmax_hours)
        )
        assert abs(cmax(150, cfg) - expected_cmax) < 1e-10


# ─── Relative vs Absolute Consistency ──────────────────────────────────

class TestConsistency:
    def test_relative_at_peak_is_100(self, cfg):
        """At Tmax, relative concentration must be 100%."""
        for dose in [50, 75, 100, 150, 200, 250]:
            rel = relative_concentration_at(cfg.tmax_hours, dose, cfg)
            assert abs(rel - 100.0) < 0.01

    def test_absolute_proportional_to_dose(self, cfg):
        """Absolute concentration scales linearly with dose."""
        t = 4.0
        c100 = concentration_at(t, 100, cfg)
        c200 = concentration_at(t, 200, cfg)
        c300 = concentration_at(t, 300, cfg)
        assert abs(c200 / c100 - 2.0) < 0.001
        assert abs(c300 / c100 - 3.0) < 0.001

    def test_relative_same_for_all_doses(self, cfg):
        """Relative (%) should be identical for any dose at same t."""
        t = 6.0
        r75 = relative_concentration_at(t, 75, cfg)
        r150 = relative_concentration_at(t, 150, cfg)
        r300 = relative_concentration_at(t, 300, cfg)
        assert abs(r75 - r150) < 0.001
        assert abs(r150 - r300) < 0.001
