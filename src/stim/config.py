"""Configuration management for stim."""

import toml
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Literal, Optional

CONFIG_PATH = Path.home() / ".stim" / "config.toml"

Sensitivity = Literal["low", "medium", "high"]
SteadyStateMode = Literal["auto", "on", "off"]

SENSITIVITY_THRESHOLDS = {
    "low": 0.35,
    "medium": 0.25,
    "high": 0.15,
}


@dataclass
class Config:
    default_dose_mg: float = 150.0
    half_life_hours: float = 15.0
    tmax_hours: float = 2.0
    sleep_time: str = "22:00"
    sleep_sensitivity: Sensitivity = "medium"
    late_dose_cutoff: str = "13:00"
    streak_warn_days: int = 3
    streak_alert_days: int = 5
    timezone: str = ""
    body_weight_kg: Optional[float] = None
    steady_state_correction: SteadyStateMode = "auto"

    @property
    def sleep_threshold(self) -> float:
        return SENSITIVITY_THRESHOLDS[self.sleep_sensitivity]

    @property
    def ke(self) -> float:
        """Elimination rate constant."""
        import math
        return math.log(2) / self.half_life_hours

    @property
    def ka(self) -> float:
        """Absorption rate constant — solved numerically from Tmax and ke."""
        import math
        ke = self.ke
        tmax = self.tmax_hours
        # Newton's method to solve: ka * exp(-ka * tmax) = ke * exp(-ke * tmax)
        ka = 1.2  # initial guess
        for _ in range(50):
            f = ka * math.exp(-ka * tmax) - ke * math.exp(-ke * tmax)
            df = math.exp(-ka * tmax) * (1 - ka * tmax)
            if abs(df) < 1e-12:
                break
            ka = ka - f / df
        return ka


def load_config() -> Config:
    """Load config from disk, creating defaults if missing."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            data = toml.load(f)
        return Config(**{k: v for k, v in data.items() if k in Config.__dataclass_fields__})
    return Config()


def save_config(cfg: Config) -> None:
    """Save config to disk."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(cfg)
    # Remove None values for cleaner config file
    data = {k: v for k, v in data.items() if v is not None}
    with open(CONFIG_PATH, "w") as f:
        toml.dump(data, f)


def ensure_config() -> Config:
    """Load config, creating default file if it doesn't exist."""
    if not CONFIG_PATH.exists():
        save_config(Config())
    return load_config()
