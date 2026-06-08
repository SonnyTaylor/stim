"""Rich display helpers and plotext graphing."""

from typing import Optional

import plotext as plt
from datetime import datetime, timezone, timedelta
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich import box

from stim.config import Config, SENSITIVITY_THRESHOLDS

console = Console(force_terminal=True)

# Fix Windows cp1252 encoding for Unicode characters
import sys
import os
if sys.platform == "win32":
    os.system("")  # Enable ANSI escape codes
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


def color_for_level(level_pct: float, cfg: Config) -> str:
    """Return Rich style for a blood level."""
    if level_pct < 15:
        return "green"
    elif level_pct < cfg.sleep_threshold * 100:
        return "yellow"
    else:
        return "red"


def color_for_streak(streak: int, cfg: Config) -> str:
    """Return Rich style for a streak count."""
    if streak >= cfg.streak_alert_days:
        return "bold red"
    elif streak >= cfg.streak_warn_days:
        return "yellow"
    return "green"


def level_bar(pct: float, width: int = 20) -> str:
    """ASCII bar for a percentage level."""
    filled = int(pct / 100 * width)
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def sparkline(values: list[float], width: int = 20) -> str:
    """Mini sparkline from a list of values."""
    if not values:
        return ""
    blocks = " ▁▂▃▄▅▆▇█"
    mn, mx = min(values), max(values)
    rng = mx - mn if mx > mn else 1
    # Sample values to width
    if len(values) >= width:
        step = len(values) / width
        sampled = [values[int(i * step)] for i in range(width)]
    else:
        sampled = values
    return "".join(blocks[min(int((v - mn) / rng * 8), 8)] for v in sampled)


# Platform-specific 12-hour format (no leading zero)
import sys as _sys
_TIME_FMT = "%#I:%M %p" if _sys.platform == "win32" else "%-I:%M %p"
_DATETIME_FMT = "%Y-%m-%d %#I:%M %p" if _sys.platform == "win32" else "%Y-%m-%d %-I:%M %p"


def format_local_time(utc_str: str, fmt: str = _TIME_FMT) -> str:
    """Convert UTC ISO string to local time display."""
    dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
    local = dt.astimezone()
    return local.strftime(fmt)


def format_local_datetime(utc_str: str, fmt: str = _DATETIME_FMT) -> str:
    """Convert UTC ISO string to local datetime display."""
    dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
    local = dt.astimezone()
    return local.strftime(fmt)


def parse_time_input(time_str: str, reference: datetime = None) -> datetime:
    """
    Parse flexible time input into a UTC datetime.
    Supports: "08:30", "yesterday", "yesterday 07:00", or full ISO.
    """
    from dateutil import parser as dp
    if reference is None:
        reference = datetime.now(timezone.utc)

    time_str = time_str.strip().lower()

    if time_str == "yesterday":
        yesterday = reference - timedelta(days=1)
        return yesterday.replace(hour=reference.hour, minute=reference.minute, second=0, microsecond=0)

    if time_str.startswith("yesterday "):
        time_part = time_str[9:]
        yesterday = reference - timedelta(days=1)
        parsed_time = dp.parse(time_part, default=yesterday)
        return parsed_time.replace(tzinfo=timezone.utc)

    # Try parsing as full date or time only
    try:
        parsed = dp.parse(time_str)
        if parsed.tzinfo is None:
            # Check if input contains a date (year-month-day pattern)
            import re
            has_date = bool(re.search(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{4}', time_str))
            if not has_date:
                # Only time provided — assume today in local time
                local_now = datetime.now().astimezone()
                parsed = parsed.replace(year=local_now.year, month=local_now.month, day=local_now.day)
            return parsed.astimezone(timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError(f"Cannot parse time: {time_str}")


def parse_mg(text: str) -> float:
    """Parse '150mg' or '150' into float."""
    text = text.strip().lower().rstrip("mg")
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"Cannot parse dose amount: {text}")


def streak_display(streak: int, cfg: Config) -> Text:
    """Rich text for streak display."""
    style = color_for_streak(streak, cfg)
    if streak == 0:
        return Text("No active streak", style="dim")
    txt = Text()
    txt.append(f"{streak} day", style=style)
    if streak != 1:
        txt.append("s", style=style)
    if streak >= cfg.streak_alert_days:
        txt.append(" ⚠ ALERT", style="bold red")
    elif streak >= cfg.streak_warn_days:
        txt.append(" ⚡ WARNING", style="yellow")
    return txt


def metabolite_warning(streak: int) -> Optional[str]:
    """Return metabolite accumulation warning if streak >= 5 days."""
    if streak >= 5:
        return (
            "⚠  Metabolite note: after 5+ consecutive days, active metabolites "
            "(especially modafinil sulfone, ~7.8× single-dose accumulation) may "
            "contribute to CNS activity not captured by this model. Actual "
            "alertness and sleep impact may be higher than the % shown."
        )
    return None


def cyp3a4_warning(streak: int) -> Optional[str]:
    """Return CYP3A4 interaction warning if streak >= 7 days."""
    if streak >= 7:
        return (
            "ℹ  CYP3A4 note: armodafinil induces CYP3A4. With chronic use, "
            "medications metabolised by CYP3A4 (e.g. some contraceptives, "
            "cyclosporine, certain antifungals) may have reduced effectiveness. "
            "Consult a prescriber if relevant."
        )
    return None


def draw_blood_graph(doses, cfg: Config, hours_back: float = 48, hours_forward: float = 12, streak: int = 0):
    """Draw concentration curve in terminal using plotext."""
    from stim.pharmaco import curve_points, cmax

    now = datetime.now(timezone.utc)
    xs, ys = curve_points(doses, -hours_back, hours_forward, 0.5, cfg, now, streak=streak)

    plt.clear_figure()
    plt.theme("pro")
    plt.plot_size(80, 20)
    plt.title("Blood Concentration Curve (% of peak)")
    plt.xlabel("Time")
    plt.ylabel("% of peak")

    # Main curve
    plt.plot(xs, ys, label="Combined level", color="cyan")

    # Sleep threshold line
    threshold = cfg.sleep_threshold * 100
    plt.horizontal_line(threshold, label=f"Sleep threshold ({threshold:.0f}%)", color="red")

    # Current time marker
    plt.vertical_line(0, label="Now", color="yellow")

    # X-axis labels
    xticks = [h for h in xs if abs(h) % 6 < 0.5]
    xlabels = []
    for h in xticks:
        t = now + timedelta(hours=h)
        xlabels.append(t.astimezone().strftime("%H:%M"))
    plt.xticks(xticks, xlabels)

    plt.ylim(0, max(max(ys) * 1.1, 10))
    plt.show()


def draw_dose_frequency(dates: list[str], counts: list[int], title: str = "Dose Frequency"):
    """Bar chart of dose counts by day."""
    plt.clear_figure()
    plt.theme("pro")
    plt.plot_size(80, 15)
    plt.title(title)
    plt.xlabel("Date")
    plt.ylabel("Doses")

    # Truncate labels for display
    short_dates = [d[-5:] for d in dates]  # MM-DD
    plt.bar(short_dates, counts, color="cyan")
    plt.show()


def draw_time_distribution(hours: list[int], title: str = "Time of Day Distribution"):
    """Histogram of dose times by hour."""
    # Build counts per hour
    hour_counts = [0] * 24
    for h in hours:
        hour_counts[h] += 1

    labels = [f"{h:02d}:00" for h in range(24)]

    plt.clear_figure()
    plt.theme("pro")
    plt.plot_size(80, 12)
    plt.title(title)
    plt.xlabel("Hour")
    plt.ylabel("Count")

    plt.bar(labels, hour_counts, color="green")
    plt.show()


def draw_calendar_grid(on_days: dict[str, bool], weeks: int = 4):
    """Visual calendar grid showing on/off days."""
    today = datetime.now().date()
    lines = []
    header = "    " + "  ".join(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
    lines.append(header)

    # Find the start of the grid (beginning of week, N weeks ago)
    start = today - timedelta(days=today.weekday() + 7 * (weeks - 1))
    current = start

    while current <= today:
        if current.weekday() == 0:
            week_label = current.strftime("%m/%d")
            line = f"{week_label} "

        day_str = current.strftime("%Y-%m-%d")
        if day_str in on_days:
            if on_days[day_str]:
                line += " ● "
            else:
                line += " ○ "
        else:
            line += " · "

        if current.weekday() == 6 or current == today:
            lines.append(line)

        current += timedelta(days=1)

    console.print("\n".join(lines))
    console.print("  ● = dose taken   ○ = off day   · = no data")
