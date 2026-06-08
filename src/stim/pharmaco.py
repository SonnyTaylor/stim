"""Pharmacokinetic calculations for armodafinil.

Model parameters:
    Vd = 45 L (Willavize et al. 2017 population PK)
    F = 0.73 (FDA label)
    ka ≈ 1.2 hr⁻¹ (back-calculated from observed Tmax=2h; absorbs implicit Tlag)
    ke = ln(2) / t½ (default t½ = 15 h → ke ≈ 0.0462 hr⁻¹)

Food effect (FDA label, Darwish 2009):
    Fed Tmax delay: +2.5 h (midpoint of 2–4 h range)
    Total AUC: unchanged

Steady-state accumulation (Lang 2025, Darwish 2009):
    Day 7+ streak: 1.5× correction multiplier
    Day 14+: 1.7× correction multiplier

Body weight correction (Willavize 2017):
    Reference weight: 70 kg
    Exponent: 0.47 (fitted to 50kg=1.164, 150kg=0.709 data points)
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from stim.config import Config

# PK constants
BIOAVAILABILITY = 0.73  # F
VD = 45.0               # Volume of distribution (L) — Willavize 2017
REFERENCE_WEIGHT_KG = 70.0
WEIGHT_EXPONENT = 0.47
FED_TMAX_SHIFT_H = 2.5  # Food delays Tmax by 2–4 h; use midpoint
PLATEAU_MULTIPLIER = 1.5  # Steady-state at day 7+ (Lang 2025)


@dataclass
class DoseEvent:
    """A single dose taken at a specific time."""
    amount_mg: float
    taken_at: datetime  # UTC
    note: Optional[str] = None
    fed: bool = False


def weight_correction_factor(weight_kg: Optional[float]) -> float:
    """
    Empirical correction factor derived from Willavize 2017 population PK.
    At 50 kg: factor = 1.164 (16.4% higher AUC)
    At 70 kg: factor = 1.000
    At 150 kg: factor = 0.709 (29.1% lower AUC)
    """
    if weight_kg is None or weight_kg <= 0:
        return 1.0
    return (REFERENCE_WEIGHT_KG / weight_kg) ** WEIGHT_EXPONENT


def steady_state_multiplier(streak: int, mode: str = "auto") -> float:
    """
    Correction multiplier for chronic daily dosing.
    Based on Lang 2025 (1.5× at day 7) and Darwish 2009 (1.8× at day 7).
    Uses conservative 1.5× from the more recent Lang 2025.
    """
    if mode == "off":
        return 1.0
    if mode == "on" or (mode == "auto" and streak >= 7):
        if streak >= 14:
            return 1.7
        elif streak >= 7:
            return 1.5
        elif streak >= 4:
            return 1.3
    return 1.0


def _concentration_raw(t_hours: float, dose_mg: float, cfg: Config, fed: bool = False) -> float:
    """
    One-compartment oral absorption model.
    Returns absolute concentration (arbitrary units).
    t_hours: time since dose in hours.
    If fed, applies Tlag to absorption start.
    """
    if t_hours < 0:
        return 0.0

    # Apply food lag: drug doesn't start absorbing until Tlag
    if fed:
        t_hours = t_hours - FED_TMAX_SHIFT_H
        if t_hours < 0:
            return 0.0

    ka = cfg.ka
    ke = cfg.ke
    denom = VD * (ka - ke)
    if abs(denom) < 1e-12:
        return 0.0
    return (BIOAVAILABILITY * dose_mg * ka) / denom * (math.exp(-ke * t_hours) - math.exp(-ka * t_hours))


def concentration_at(t_hours: float, dose_mg: float, cfg: Config, fed: bool = False) -> float:
    """Concentration at time t after a single dose."""
    return _concentration_raw(t_hours, dose_mg, cfg, fed)


def cmax(dose_mg: float, cfg: Config, fed: bool = False) -> float:
    """Peak concentration for a given dose."""
    tmax = cfg.tmax_hours + (FED_TMAX_SHIFT_H if fed else 0)
    return _concentration_raw(tmax, dose_mg, cfg, fed)


def relative_concentration_at(t_hours: float, dose_mg: float, cfg: Config, fed: bool = False) -> float:
    """Concentration as % of peak for a single dose."""
    peak = cmax(dose_mg, cfg, fed)
    if peak <= 0:
        return 0.0
    return _concentration_raw(t_hours, dose_mg, cfg, fed) / peak * 100.0


def combined_concentration_at(
    t_hours_from_now: float,
    doses: list[DoseEvent],
    cfg: Config,
    now: Optional[datetime] = None,
    apply_weight: bool = True,
    streak: int = 0,
) -> float:
    """
    Sum of concentrations from all doses at a given offset from now.
    t_hours_from_now: 0 = now, positive = future, negative = past.
    Returns combined relative concentration (% of a reference single dose peak).
    Applies weight correction and steady-state multiplier if configured.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    ref_peak = cmax(cfg.default_dose_mg, cfg)
    if ref_peak <= 0:
        return 0.0

    target_time = now + timedelta(hours=t_hours_from_now)
    total = 0.0
    for d in doses:
        hours_since = (target_time - d.taken_at).total_seconds() / 3600.0
        total += _concentration_raw(hours_since, d.amount_mg, cfg, d.fed)

    # Apply weight correction
    if apply_weight:
        total *= weight_correction_factor(cfg.body_weight_kg)

    # Apply steady-state accumulation
    total *= steady_state_multiplier(streak, cfg.steady_state_correction)

    return total / ref_peak * 100.0


def combined_absolute_at(
    t_hours_from_now: float,
    doses: list[DoseEvent],
    cfg: Config,
    now: Optional[datetime] = None,
) -> float:
    """Sum of absolute concentrations from all doses at a given offset from now."""
    if now is None:
        now = datetime.now(timezone.utc)

    target_time = now + timedelta(hours=t_hours_from_now)
    total = 0.0
    for d in doses:
        hours_since = (target_time - d.taken_at).total_seconds() / 3600.0
        total += _concentration_raw(hours_since, d.amount_mg, cfg, d.fed)
    return total


def current_level(doses: list[DoseEvent], cfg: Config, streak: int = 0, now: Optional[datetime] = None) -> float:
    """Current combined blood level as % of reference dose peak."""
    return combined_concentration_at(0, doses, cfg, now=now, streak=streak)


def time_to_threshold(
    doses: list[DoseEvent],
    threshold_pct: float,
    cfg: Config,
    now: Optional[datetime] = None,
    streak: int = 0,
) -> Optional[datetime]:
    """
    Find when combined level drops below threshold (% of reference peak).
    Returns None if already below, or the datetime when it crosses.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Check if already below
    if combined_concentration_at(0, doses, cfg, now, streak=streak) < threshold_pct:
        return now

    # Search forward in 15-min increments up to 72 hours
    for step in range(1, 289):  # 72h * 4
        t = step * 0.25
        if combined_concentration_at(t, doses, cfg, now, streak=streak) < threshold_pct:
            return now + timedelta(hours=t)

    return None


def level_at_time(
    target_time: datetime,
    doses: list[DoseEvent],
    cfg: Config,
    streak: int = 0,
) -> float:
    """Combined level at a specific future time, as % of reference peak."""
    ref_peak = cmax(cfg.default_dose_mg, cfg)
    if ref_peak <= 0:
        return 0.0
    total = 0.0
    for d in doses:
        hours_since = (target_time - d.taken_at).total_seconds() / 3600.0
        total += _concentration_raw(hours_since, d.amount_mg, cfg, d.fed)

    # Apply weight correction
    total *= weight_correction_factor(cfg.body_weight_kg)
    # Apply steady-state
    total *= steady_state_multiplier(streak, cfg.steady_state_correction)

    return total / ref_peak * 100.0


def curve_points(
    doses: list[DoseEvent],
    hours_start: float,
    hours_end: float,
    step_hours: float,
    cfg: Config,
    now: Optional[datetime] = None,
    streak: int = 0,
) -> tuple[list[float], list[float]]:
    """
    Generate curve data points.
    Returns (hours_offsets, concentrations_pct).
    Hours are relative to now (negative = past, positive = future).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    xs = []
    ys = []
    t = hours_start
    while t <= hours_end:
        xs.append(t)
        ys.append(combined_concentration_at(t, doses, cfg, now, streak=streak))
        t += step_hours
    return xs, ys


def clearance_time(
    doses: list[DoseEvent],
    threshold_pct: float = 10.0,
    cfg: Config = None,
    now: Optional[datetime] = None,
    streak: int = 0,
) -> Optional[datetime]:
    """When will current level drop below threshold % of peak."""
    if cfg is None:
        cfg = Config()
    return time_to_threshold(doses, threshold_pct, cfg, now, streak=streak)
