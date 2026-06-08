"""CLI entry point for stim."""

import math
from datetime import datetime, timedelta, timezone
from typing import Optional

import typer
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from stim import __version__
from stim.config import Config, ensure_config, save_config, SENSITIVITY_THRESHOLDS
from stim.db import get_connection, init_db, now_utc
from stim.pharmaco import (
    DoseEvent, concentration_at, cmax, relative_concentration_at,
    combined_concentration_at, current_level, time_to_threshold,
    level_at_time, curve_points, clearance_time,
    steady_state_multiplier, weight_correction_factor,
)
from stim.display import (
    console, color_for_level, color_for_streak, level_bar, sparkline,
    format_local_time, format_local_datetime, parse_time_input, parse_mg,
    streak_display, draw_blood_graph, draw_dose_frequency,
    draw_time_distribution, draw_calendar_grid,
    metabolite_warning, cyp3a4_warning, _TIME_FMT,
)

HELP_TEXT = """[bold]stim[/bold] — armodafinil usage tracker

[dim]Log doses, check blood levels, and stay safe.[/dim]

[bold cyan]Quick start:[/bold cyan]
  stim log 150mg              Log a dose right now
  stim log 75mg 08:30         Log with a specific time
  stim today                  See today's summary
  stim blood                  Current blood level estimate
  stim status                 Full safety snapshot

[bold cyan]Learn more:[/bold cyan]
  stim log --help             See all logging options
  stim calc --help            Calculator modes
  stim config                 View/change settings
"""

app = typer.Typer(
    name="stim",
    help=HELP_TEXT,
    add_completion=False,
    rich_markup_mode="rich",
)
cfg: Config


def version_callback(value: bool) -> None:
    if value:
        console.print(f"[bold]stim[/bold] v{__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", "-v", callback=version_callback, is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Initialize database and config on first run."""
    global cfg
    init_db()
    cfg = ensure_config()
    if ctx.invoked_subcommand is None:
        console.print(HELP_TEXT)


# ─── Helpers ────────────────────────────────────────────────────────────

def get_doses(days: Optional[int] = None) -> list[DoseEvent]:
    """Fetch doses from DB, optionally limited to last N days."""
    with get_connection() as conn:
        if days:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            rows = conn.execute(
                "SELECT amount_mg, taken_at, note, is_off_day, fed FROM doses WHERE taken_at >= ? ORDER BY taken_at",
                (cutoff,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT amount_mg, taken_at, note, is_off_day, fed FROM doses ORDER BY taken_at"
            ).fetchall()

    events = []
    for r in rows:
        if r["is_off_day"]:
            continue
        dt = datetime.fromisoformat(r["taken_at"].replace("Z", "+00:00"))
        events.append(DoseEvent(
            amount_mg=r["amount_mg"],
            taken_at=dt,
            note=r["note"],
            fed=bool(r["fed"]),
        ))
    return events


def get_today_doses() -> list[dict]:
    """Get raw dose rows for today (including off-day markers). Uses local time boundary."""
    local_now = datetime.now().astimezone()
    today_start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = today_start_local.astimezone(timezone.utc)
    with get_connection() as conn:
        return conn.execute(
            "SELECT id, amount_mg, taken_at, note, is_off_day, fed FROM doses WHERE taken_at >= ? ORDER BY taken_at",
            (today_start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),),
        ).fetchall()


def compute_streak() -> int:
    """Compute current consecutive dosing streak in days."""
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT DISTINCT date(taken_at) as dose_date
            FROM doses
            WHERE is_off_day = 0 AND amount_mg > 0
            ORDER BY dose_date DESC
        """).fetchall()

    if not rows:
        return 0

    dates = [r["dose_date"] for r in rows]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    if dates[0] != today and dates[0] != yesterday:
        return 0

    streak = 0
    expected = datetime.fromisoformat(dates[0])
    for d in dates:
        if d == expected.strftime("%Y-%m-%d"):
            streak += 1
            expected -= timedelta(days=1)
        else:
            break

    with get_connection() as conn:
        off_rows = conn.execute("""
            SELECT DISTINCT date(taken_at) as off_date
            FROM doses WHERE is_off_day = 1
            ORDER BY off_date DESC
        """).fetchall()

    off_dates = {r["off_date"] for r in off_rows}
    check = datetime.fromisoformat(dates[0])
    for _ in range(streak):
        if check.strftime("%Y-%m-%d") in off_dates:
            streak -= 1
        check -= timedelta(days=1)

    return max(0, streak)


def check_sleep_warning(dose_mg: float, taken_at: datetime, fed: bool = False) -> None:
    """Print sleep impact warning if dose will disrupt sleep."""
    parts = cfg.sleep_time.split(":")
    sleep_hour, sleep_min = int(parts[0]), int(parts[1])
    local_now = datetime.now().astimezone()
    sleep_target = local_now.replace(hour=sleep_hour, minute=sleep_min, second=0, microsecond=0)
    if sleep_target <= local_now:
        sleep_target += timedelta(days=1)

    sleep_utc = sleep_target.astimezone(timezone.utc)
    hours_to_sleep = (sleep_utc - taken_at).total_seconds() / 3600.0

    if hours_to_sleep <= 0:
        return

    level_at_sleep = relative_concentration_at(hours_to_sleep, dose_mg, cfg, fed=fed)
    threshold = cfg.sleep_threshold * 100

    if level_at_sleep > threshold:
        style = "red" if level_at_sleep > 40 else "yellow"
        console.print(
            f"[{style}]⚠ At your target sleep time ({cfg.sleep_time}), "
            f"estimated level will be ~{level_at_sleep:.0f}% of peak. "
            f"This may disrupt sleep.[/{style}]"
        )


def check_late_dose() -> None:
    """Print warning if dosing after cutoff time."""
    parts = cfg.late_dose_cutoff.split(":")
    cutoff_hour, cutoff_min = int(parts[0]), int(parts[1])
    local_now = datetime.now().astimezone()
    if local_now.hour > cutoff_hour or (local_now.hour == cutoff_hour and local_now.minute >= cutoff_min):
        console.print(
            f"[yellow]⚠ Late dose — it's past your cutoff time ({cfg.late_dose_cutoff}).[/yellow]"
        )


# ─── Commands: Logging ─────────────────────────────────────────────────

LOG_HELP = """[bold]Log a dose.[/bold]

[bold cyan]Examples:[/bold cyan]
  stim log 150mg                    Log 150mg right now
  stim log 75mg 08:30               Log 75mg at 8:30am today
  stim log 150mg yesterday          Log for yesterday at current time
  stim log 150mg "yesterday 07:00"  Log for yesterday at 7am
  stim log 150mg --fed              Log with food (delays absorption ~2.5h)
  stim log 150mg --note "tired"     Log with a note
  stim log 75mg --fed -n "late"     Combine flags

[dim]Time accepts: "08:30", "yesterday", "yesterday 07:00", or full ISO dates.[/dim]
"""


@app.command(help=LOG_HELP)
def log(
    amount: str = typer.Argument(..., help="Dose amount, e.g. '150mg' or '150'."),
    time: Optional[str] = typer.Argument(None, help="Time: '08:30', 'yesterday', 'yesterday 07:00'."),
    note: Optional[str] = typer.Option(None, "--note", "-n", help="Optional note."),
    fed: bool = typer.Option(False, "--fed", "-f", help="Dose taken with food (delays absorption ~2.5h)."),
) -> None:
    """Log a dose."""
    dose_mg = parse_mg(amount)

    if time:
        taken_at = parse_time_input(time)
    else:
        taken_at = datetime.now(timezone.utc)

    taken_at_str = taken_at.strftime("%Y-%m-%dT%H:%M:%SZ")

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO doses (amount_mg, taken_at, note, is_off_day, fed) VALUES (?, ?, ?, 0, ?)",
            (dose_mg, taken_at_str, note, int(fed)),
        )

    local_time = taken_at.astimezone().strftime(_TIME_FMT)
    date_str = taken_at.astimezone().strftime("%Y-%m-%d")
    fed_str = " [dim](fed)[/dim]" if fed else ""

    console.print(f"[green]✓[/green] Logged [bold]{dose_mg:.0f}mg[/bold] at {local_time} on {date_str}{fed_str}")
    if note:
        console.print(f"  [dim]Note: {note}[/dim]")

    check_late_dose()
    check_sleep_warning(dose_mg, taken_at, fed=fed)


@app.command(name="off", help="Mark today as an intentional off day. Resets streak counting.")
def off_day(
    time: Optional[str] = typer.Argument(None, help="Date, e.g. 'yesterday'. Defaults to today."),
) -> None:
    """Mark today (or a given day) as an intentional off day."""
    if time:
        taken_at = parse_time_input(time)
    else:
        taken_at = datetime.now(timezone.utc)

    taken_at_str = taken_at.strftime("%Y-%m-%dT%H:%M:%SZ")

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO doses (amount_mg, taken_at, note, is_off_day) VALUES (0, ?, 'off day', 1)",
            (taken_at_str,),
        )

    date_str = taken_at.astimezone().strftime("%Y-%m-%d")
    console.print(f"[green]✓[/green] Marked [bold]{date_str}[/bold] as off day.")


@app.command(help="Remove the most recent dose entry (with confirmation).")
def undo() -> None:
    """Remove the most recent dose entry."""
    with get_connection() as conn:
        row = conn.execute("SELECT id, amount_mg, taken_at, is_off_day FROM doses ORDER BY id DESC LIMIT 1").fetchone()

    if not row:
        console.print("[dim]Nothing to undo.[/dim]")
        return

    local_time = format_local_datetime(row["taken_at"])
    if row["is_off_day"]:
        desc = f"off-day marker from {local_time}"
    else:
        desc = f"{row['amount_mg']:.0f}mg dose from {local_time}"

    confirm = typer.confirm(f"Remove {desc}?")
    if not confirm:
        console.print("[dim]Cancelled.[/dim]")
        return

    with get_connection() as conn:
        conn.execute("DELETE FROM doses WHERE id = ?", (row["id"],))

    console.print(f"[green]✓[/green] Removed {desc}.")


# ─── Commands: Viewing ──────────────────────────────────────────────────

TODAY_HELP = """[bold]Today's summary.[/bold]

Shows doses logged today, current blood level estimate, and streak info.

[bold cyan]Example:[/bold cyan]
  stim today
"""


@app.command(help=TODAY_HELP)
def today() -> None:
    """Show today's summary."""
    rows = get_today_doses()
    doses = get_doses()
    streak = compute_streak()

    lines = []

    if not rows:
        lines.append("[dim]No doses logged today.[/dim]")
    else:
        for r in rows:
            if r["is_off_day"]:
                lines.append("[dim]○ Off day marker[/dim]")
            else:
                t = format_local_time(r["taken_at"])
                note_str = f" — [dim]{r['note']}[/dim]" if r["note"] else ""
                fed_str = " [dim](fed)[/dim]" if r["fed"] else ""
                lines.append(f"[green]●[/green] {r['amount_mg']:.0f}mg at {t}{fed_str}{note_str}")

    level = current_level(doses, cfg, streak=streak)
    style = color_for_level(level, cfg)
    bar = level_bar(level)
    lines.append("")
    lines.append(f"Blood level: [{style}]{bar} {level:.1f}%[/{style}] of peak")
    lines.append(f"Streak: {streak_display(streak, cfg)}")

    mw = metabolite_warning(streak)
    if mw:
        lines.append("")
        lines.append(f"[yellow]{mw}[/yellow]")

    panel = Panel("\n".join(lines), title="Today", border_style="cyan", padding=(1, 2))
    console.print(panel)


HISTORY_HELP = """[bold]View dose history.[/bold]

[bold cyan]Examples:[/bold cyan]
  stim history                  All doses (table)
  stim history --days 14        Last 14 days only
  stim history --graph          Dose amount chart over time
  stim history --days 30 --graph  Last 30 days as chart
"""


@app.command(help=HISTORY_HELP)
def history(
    days: Optional[int] = typer.Option(None, "--days", "-d", help="Limit to last N days."),
    graph: bool = typer.Option(False, "--graph", "-g", help="Show dose chart over time."),
) -> None:
    """Show dose history table."""
    with get_connection() as conn:
        if days:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            rows = conn.execute(
                "SELECT id, amount_mg, taken_at, note, is_off_day, fed FROM doses WHERE taken_at >= ? ORDER BY taken_at",
                (cutoff,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, amount_mg, taken_at, note, is_off_day, fed FROM doses ORDER BY taken_at"
            ).fetchall()

    if not rows:
        console.print("[dim]No doses recorded.[/dim]")
        return

    if graph:
        # Build dose data for charting
        dates = []
        amounts = []
        for r in rows:
            if r["is_off_day"]:
                continue
            local_dt = datetime.fromisoformat(r["taken_at"].replace("Z", "+00:00")).astimezone()
            dates.append(local_dt.strftime("%Y-%m-%d"))
            amounts.append(r["amount_mg"])

        if not dates:
            console.print("[dim]No doses to chart.[/dim]")
            return

        # Group by date and sum amounts per day
        daily_totals: dict[str, float] = {}
        for d, a in zip(dates, amounts):
            daily_totals[d] = daily_totals.get(d, 0) + a

        # Sort by date
        sorted_dates = sorted(daily_totals.keys())
        sorted_amounts = [daily_totals[d] for d in sorted_dates]

        # Fill in missing days
        if len(sorted_dates) > 1:
            start = datetime.strptime(sorted_dates[0], "%Y-%m-%d").date()
            end = datetime.strptime(sorted_dates[-1], "%Y-%m-%d").date()
            all_dates = []
            all_amounts = []
            current = start
            while current <= end:
                ds = current.strftime("%Y-%m-%d")
                all_dates.append(ds)
                all_amounts.append(daily_totals.get(ds, 0))
                current += timedelta(days=1)
            sorted_dates = all_dates
            sorted_amounts = all_amounts

        import plotext as plt
        plt.clear_figure()
        plt.theme("pro")
        plt.plot_size(80, 15)
        plt.title("Dose History (mg per day)")
        plt.xlabel("Date")
        plt.ylabel("mg")

        short_dates = [d[-5:] for d in sorted_dates]
        plt.bar(short_dates, sorted_amounts, color="green")
        plt.show()
        return

    # Table view (original)
    # Re-fetch in descending order for table display
    with get_connection() as conn:
        if days:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            rows = conn.execute(
                "SELECT id, amount_mg, taken_at, note, is_off_day, fed FROM doses WHERE taken_at >= ? ORDER BY taken_at DESC",
                (cutoff,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, amount_mg, taken_at, note, is_off_day, fed FROM doses ORDER BY taken_at DESC"
            ).fetchall()

    table = Table(title="Dose History", box=box.ROUNDED)
    table.add_column("#", style="dim", justify="right")
    table.add_column("Date", style="cyan")
    table.add_column("Time", style="cyan")
    table.add_column("Dose", style="green", justify="right")
    table.add_column("Fed", style="dim")
    table.add_column("Note", style="dim")

    for r in rows:
        local_dt = datetime.fromisoformat(r["taken_at"].replace("Z", "+00:00")).astimezone()
        date_str = local_dt.strftime("%Y-%m-%d")
        time_str = local_dt.strftime(_TIME_FMT)
        if r["is_off_day"]:
            dose_str = "[dim]off day[/dim]"
        else:
            dose_str = f"{r['amount_mg']:.0f}mg"
        fed_str = "●" if r["fed"] else ""
        note_str = r["note"] or ""
        table.add_row(str(r["id"]), date_str, time_str, dose_str, fed_str, note_str)

    console.print(table)


STATUS_HELP = """[bold]Safety snapshot.[/bold]

Shows streak, weekly intake, blood level estimate, and any safety warnings.

[bold cyan]Examples:[/bold cyan]
  stim status
"""


@app.command(help=STATUS_HELP)
def status() -> None:
    """Safety snapshot with streak, weekly summary, and blood level."""
    doses = get_doses()
    streak = compute_streak()
    level = current_level(doses, cfg, streak=streak)

    now = datetime.now(timezone.utc)
    week_start = now - timedelta(days=now.weekday())
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    last_week_start = week_start - timedelta(days=7)

    with get_connection() as conn:
        this_week = conn.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(amount_mg), 0) as total FROM doses WHERE taken_at >= ? AND is_off_day = 0",
            (week_start.strftime("%Y-%m-%dT%H:%M:%SZ"),),
        ).fetchone()
        last_week = conn.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(amount_mg), 0) as total FROM doses WHERE taken_at >= ? AND taken_at < ? AND is_off_day = 0",
            (last_week_start.strftime("%Y-%m-%dT%H:%M:%SZ"), week_start.strftime("%Y-%m-%dT%H:%M:%SZ")),
        ).fetchone()

    lines = []

    lines.append(f"Streak: {streak_display(streak, cfg)}")
    lines.append("")
    lines.append(f"This week:  [cyan]{this_week['cnt']}[/cyan] doses, [cyan]{this_week['total']:.0f}mg[/cyan] total")
    lines.append(f"Last week:  [dim]{last_week['cnt']} doses, {last_week['total']:.0f}mg total[/dim]")
    lines.append("")

    level_style = color_for_level(level, cfg)
    bar = level_bar(level)
    lines.append(f"Blood level: [{level_style}]{bar} {level:.1f}%[/{level_style}] of peak")

    if streak >= 4:
        mult = steady_state_multiplier(streak, cfg.steady_state_correction)
        if mult > 1.0:
            lines.append(f"  [dim]Adjusted ×{mult:.1f} for steady-state accumulation (day {streak})[/dim]")

    if cfg.body_weight_kg:
        wcf = weight_correction_factor(cfg.body_weight_kg)
        lines.append(f"  [dim]Weight-adjusted for {cfg.body_weight_kg:.0f}kg (factor {wcf:.3f})[/dim]")

    parts = cfg.sleep_time.split(":")
    sleep_hour, sleep_min = int(parts[0]), int(parts[1])
    local_now = datetime.now().astimezone()
    sleep_target = local_now.replace(hour=sleep_hour, minute=sleep_min, second=0, microsecond=0)
    if sleep_target <= local_now:
        sleep_target += timedelta(days=1)

    if doses:
        sleep_utc = sleep_target.astimezone(timezone.utc)
        sleep_level = level_at_time(sleep_utc, doses, cfg, streak=streak)
        sleep_style = color_for_level(sleep_level, cfg)
        lines.append(f"Est. at {cfg.sleep_time}: [{sleep_style}]{sleep_level:.1f}%[/{sleep_style}] of peak")
    lines.append("")

    lines.append(f"[dim]Half-life: {cfg.half_life_hours}h | Sensitivity: {cfg.sleep_sensitivity} | Threshold: {cfg.sleep_threshold*100:.0f}%[/dim]")

    mw = metabolite_warning(streak)
    cw = cyp3a4_warning(streak)
    if mw or cw:
        lines.append("")
    if mw:
        lines.append(f"[yellow]{mw}[/yellow]")
    if cw:
        lines.append(f"[dim]{cw}[/dim]")

    border = "red" if streak >= cfg.streak_alert_days else ("yellow" if streak >= cfg.streak_warn_days else "green")
    panel = Panel("\n".join(lines), title="Status", border_style=border, padding=(1, 2))
    console.print(panel)


# ─── Commands: Stats & Graphs ───────────────────────────────────────────

STATS_HELP = """[bold]Usage stats with graphs.[/bold]

Shows dose frequency by day, time-of-day distribution, and an on/off calendar grid.

[bold cyan]Examples:[/bold cyan]
  stim stats                   Last 30 days (default)
  stim stats --week            This week only
  stim stats --month           Last 30 days
"""


@app.command(help=STATS_HELP)
def stats(
    week: bool = typer.Option(False, "--week", "-w", help="This week only."),
    month: bool = typer.Option(False, "--month", "-m", help="Last 30 days."),
) -> None:
    """Show stats with graphs."""
    days = 7 if week else (30 if month else 30)
    doses_raw = get_doses(days=days)

    if not doses_raw:
        console.print("[dim]No doses in this period.[/dim]")
        return

    date_counts: dict[str, int] = {}
    for d in doses_raw:
        local_date = d.taken_at.astimezone().strftime("%Y-%m-%d")
        date_counts[local_date] = date_counts.get(local_date, 0) + 1

    start = datetime.now().astimezone().date() - timedelta(days=days - 1)
    all_dates = []
    all_counts = []
    current = start
    today = datetime.now().astimezone().date()
    while current <= today:
        ds = current.strftime("%Y-%m-%d")
        all_dates.append(ds)
        all_counts.append(date_counts.get(ds, 0))
        current += timedelta(days=1)

    console.print("[bold]📊 Dose Frequency[/bold]")
    draw_dose_frequency(all_dates, all_counts, f"Dose Frequency (last {days} days)")
    console.print()

    hours = [d.taken_at.astimezone().hour for d in doses_raw]
    console.print("[bold]🕐 Time of Day[/bold]")
    draw_time_distribution(hours)
    console.print()

    console.print("[bold]📅 On/Off Days[/bold]")
    on_days: dict[str, bool] = {}
    with get_connection() as conn:
        all_entries = conn.execute(
            "SELECT taken_at, is_off_day, amount_mg FROM doses WHERE taken_at >= ? ORDER BY taken_at",
            ((datetime.now(timezone.utc) - timedelta(days=28)).strftime("%Y-%m-%dT%H:%M:%SZ"),),
        ).fetchall()

    for e in all_entries:
        local_date = datetime.fromisoformat(e["taken_at"].replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
        if e["is_off_day"]:
            on_days[local_date] = False
        elif e["amount_mg"] > 0:
            on_days[local_date] = True

    draw_calendar_grid(on_days, weeks=4)
    console.print()

    total_mg = sum(d.amount_mg for d in doses_raw)
    avg_dose = total_mg / len(doses_raw) if doses_raw else 0
    days_with_doses = len(date_counts)
    avg_per_day = len(doses_raw) / max(days_with_doses, 1)
    fed_count = sum(1 for d in doses_raw if d.fed)

    console.print(f"[bold]📈 Summary (last {days} days)[/bold]")
    console.print(f"  Total doses: [cyan]{len(doses_raw)}[/cyan]")
    console.print(f"  Total intake: [cyan]{total_mg:.0f}mg[/cyan]")
    console.print(f"  Average dose: [cyan]{avg_dose:.0f}mg[/cyan]")
    console.print(f"  Days with doses: [cyan]{days_with_doses}[/cyan] of {days}")
    console.print(f"  Avg doses/day: [cyan]{avg_per_day:.1f}[/cyan]")
    if fed_count:
        console.print(f"  Fed doses: [cyan]{fed_count}[/cyan] ({fed_count/len(doses_raw)*100:.0f}%)")


# ─── Commands: Blood Level ─────────────────────────────────────────────

BLOOD_HELP = """[bold]Current blood level estimate.[/bold]

Shows estimated armodafinil concentration as % of peak, with a 24h sparkline trend.

[bold cyan]Examples:[/bold cyan]
  stim blood                   Current level + sparkline
  stim blood --graph           Full concentration curve (24h back, 24h forward)
  stim blood -g --back 48      Show 48h into the past
  stim blood -g --forward 48   Show 48h into the future
  stim blood --table           Hour-by-hour breakdown (next 24h)
  stim blood --table -d 48     Hour-by-hour for next 48 hours
"""


@app.command(help=BLOOD_HELP)
def blood(
    graph: bool = typer.Option(False, "--graph", "-g", help="Show full concentration curve."),
    table: bool = typer.Option(False, "--table", "-t", help="Show hour-by-hour breakdown."),
    duration: int = typer.Option(24, "--duration", "-d", help="Hours to show in table (default: 24)."),
    hours_back: int = typer.Option(24, "--back", help="Hours to show before now in graph (default: 24)."),
    hours_forward: int = typer.Option(24, "--forward", help="Hours to show after now in graph (default: 24)."),
) -> None:
    """Show current estimated blood concentration."""
    doses = get_doses()
    streak = compute_streak()
    level = current_level(doses, cfg, streak=streak)

    if table:
        # Hour-by-hour table
        now = datetime.now(timezone.utc)
        local_now = datetime.now().astimezone()

        t = Table(title=f"Blood Level — Next {duration}h", box=box.ROUNDED)
        t.add_column("Time", style="cyan")
        t.add_column("Hours", style="dim", justify="right")
        t.add_column("Level", style="green", justify="right")
        t.add_column("", style="green")  # bar

        for h in range(0, duration + 1):
            conc = combined_concentration_at(h, doses, cfg, now, streak=streak)
            future_time = (local_now + timedelta(hours=h)).strftime(_TIME_FMT)
            bar = level_bar(conc, width=20)
            style = color_for_level(conc, cfg)

            if h == 0:
                label = "[bold]now[/bold]"
            elif h == 1:
                label = "1h"
            else:
                label = f"{h}h"

            t.add_row(
                future_time,
                label,
                f"[{style}]{conc:.1f}%[/{style}]",
                f"[{style}]{bar}[/{style}]",
            )

        console.print(t)
        return

    if graph:
        console.print("[bold]📈 Blood Concentration Curve[/bold]")
        draw_blood_graph(doses, cfg, hours_back=hours_back, hours_forward=hours_forward, streak=streak)
        console.print()

    style = color_for_level(level, cfg)
    bar = level_bar(level, width=30)

    now = datetime.now(timezone.utc)
    spark_vals = []
    for h in range(-24, 1):
        spark_vals.append(combined_concentration_at(h, doses, cfg, now, streak=streak))
    spark = sparkline(spark_vals)

    lines = [
        f"[{style}]{bar} {level:.1f}%[/{style}] of peak",
        "",
        f"Last 24h trend: {spark}",
    ]

    if doses:
        last = doses[-1]
        last_local = last.taken_at.astimezone().strftime(_TIME_FMT)
        fed_str = " (fed)" if last.fed else ""
        lines.append(f"Last dose: [cyan]{last.amount_mg:.0f}mg[/cyan] at {last_local}{fed_str}")

    if streak >= 4:
        mult = steady_state_multiplier(streak, cfg.steady_state_correction)
        if mult > 1.0:
            raw_level = current_level(doses, cfg, streak=0)
            lines.append(f"")
            lines.append(f"[dim]Adjusted ×{mult:.1f} for steady-state accumulation (day {streak}).[/dim]")
            lines.append(f"[dim]Raw single-dose model would show {raw_level:.1f}%.[/dim]")

    if cfg.body_weight_kg:
        lines.append(f"[dim]Level adjusted for body weight ({cfg.body_weight_kg:.0f}kg).[/dim]")

    mw = metabolite_warning(streak)
    if mw:
        lines.append("")
        lines.append(f"[yellow]{mw}[/yellow]")

    panel = Panel("\n".join(lines), title="Blood Level", border_style=style.replace("bold ", ""), padding=(1, 2))
    console.print(panel)


# ─── Commands: Calculators ─────────────────────────────────────────────

CALC_HELP = """[bold]Pharmacokinetic calculators.[/bold]

[bold cyan]Modes:[/bold cyan]

  [green]sleep[/green]   Can I sleep at a given time?
         stim calc sleep 22:00
         stim calc sleep 23:45

  [green]dose[/green]    When should I dose if I wake at a certain time?
         stim calc dose 06:00
         stim calc dose 07:30

  [green]clear[/green]   When will my level drop below 10% of peak?
         stim calc clear

  [green]stack[/green]   What if I take another dose right now?
         stim calc stack 75mg
         stim calc stack 75mg 14:00
"""


@app.command(name="calc", help=CALC_HELP)
def calc_sleep(
    mode: str = typer.Argument(..., help="Calculator mode: sleep, dose, clear, stack."),
    target: str = typer.Argument(..., help="Target time or dose amount."),
    extra: Optional[str] = typer.Argument(None, help="Additional parameter."),
) -> None:
    """Run pharmacokinetic calculators."""
    now = datetime.now(timezone.utc)
    local_now = datetime.now().astimezone()
    streak = compute_streak()

    if mode == "sleep":
        parts = target.split(":")
        sleep_hour, sleep_min = int(parts[0]), int(parts[1])
        sleep_target = local_now.replace(hour=sleep_hour, minute=sleep_min, second=0, microsecond=0)
        if sleep_target <= local_now:
            sleep_target += timedelta(days=1)

        doses = get_doses()
        sleep_utc = sleep_target.astimezone(timezone.utc)
        level_at_sleep = level_at_time(sleep_utc, doses, cfg, streak=streak)
        style = color_for_level(level_at_sleep, cfg)

        console.print(f"[bold]😴 Sleep Calculator[/bold]")
        console.print(f"  Target sleep: {sleep_target.strftime('%H:%M')}")
        console.print(f"  Est. level:   [{style}]{level_at_sleep:.1f}%[/{style}] of peak")
        console.print(f"  Threshold:    {cfg.sleep_threshold*100:.0f}% ({cfg.sleep_sensitivity})")

        if level_at_sleep > cfg.sleep_threshold * 100:
            safe_time = time_to_threshold(doses, cfg.sleep_threshold * 100, cfg, now, streak=streak)
            if safe_time:
                safe_local = safe_time.astimezone().strftime(_TIME_FMT)
                console.print(f"  [yellow]⚠ Not safe. Level drops below threshold around {safe_local}.[/yellow]")
            else:
                console.print(f"  [red]⚠ Not safe. Unable to determine safe time.[/red]")
        else:
            console.print(f"  [green]✓ Should be safe for sleep.[/green]")

    elif mode == "dose":
        parts = target.split(":")
        wake_hour, wake_min = int(parts[0]), int(parts[1])
        wake_time = local_now.replace(hour=wake_hour, minute=wake_min, second=0, microsecond=0)
        if wake_time <= local_now:
            wake_time += timedelta(days=1)

        parts_sleep = cfg.sleep_time.split(":")
        sleep_hour, sleep_min = int(parts_sleep[0]), int(parts_sleep[1])
        sleep_time = local_now.replace(hour=sleep_hour, minute=sleep_min, second=0, microsecond=0)
        if sleep_time <= wake_time:
            sleep_time += timedelta(days=1)

        console.print(f"[bold]⏰ Dose Timing Calculator[/bold]")
        console.print(f"  Wake time:     {wake_time.strftime('%H:%M')}")
        console.print(f"  Sleep target:  {sleep_time.strftime('%H:%M')}")
        console.print(f"  Default dose:  {cfg.default_dose_mg:.0f}mg")

        wake_utc = wake_time.astimezone(timezone.utc)
        sleep_utc = sleep_time.astimezone(timezone.utc)
        hours_awake = (sleep_utc - wake_utc).total_seconds() / 3600.0

        level_at_sleep = relative_concentration_at(hours_awake, cfg.default_dose_mg, cfg)
        style = color_for_level(level_at_sleep, cfg)
        console.print(f"  If dosing at {wake_time.strftime('%H:%M')}:")
        console.print(f"    Level at sleep: [{style}]{level_at_sleep:.1f}%[/{style}] of peak")

        if level_at_sleep > cfg.sleep_threshold * 100:
            threshold = cfg.sleep_threshold
            hours_needed = 0
            for h in range(0, 24):
                if relative_concentration_at(h, cfg.default_dose_mg, cfg) / 100 < threshold:
                    hours_needed = h
                    break

            latest_safe = sleep_time - timedelta(hours=hours_needed)
            if latest_safe < wake_time:
                console.print(f"  [yellow]⚠ Even dosing at wake time may affect sleep.[/yellow]")
            else:
                console.print(f"  [green]✓ Latest safe dose time: {latest_safe.strftime('%H:%M')}[/green]")
        else:
            console.print(f"  [green]✓ Dosing at wake time should be fine.[/green]")

    elif mode == "clear":
        doses = get_doses()
        clear_time = clearance_time(doses, 10.0, cfg, now, streak=streak)

        console.print("[bold]🧹 Clearance Calculator[/bold]")
        level = current_level(doses, cfg, streak=streak)
        console.print(f"  Current level: {level:.1f}% of peak")

        if clear_time:
            local_clear = clear_time.astimezone().strftime("%Y-%m-%d %H:%M")
            hours = (clear_time - now).total_seconds() / 3600.0
            console.print(f"  Below 10% at:  {local_clear} ({hours:.1f}h from now)")
        else:
            console.print(f"  [dim]Already below 10% or unable to calculate.[/dim]")

    elif mode == "stack":
        stack_mg = parse_mg(target)
        stack_time = now
        stack_fed = False

        if extra:
            if extra.startswith("--fed"):
                stack_fed = True
            else:
                stack_time = parse_time_input(extra, now)

        doses = get_doses()
        hypothetical = doses + [DoseEvent(amount_mg=stack_mg, taken_at=stack_time, fed=stack_fed)]

        console.print(f"[bold]📚 Stack Calculator[/bold]")
        local_stack = stack_time.astimezone().strftime(_TIME_FMT)
        fed_str = " [dim](fed)[/dim]" if stack_fed else ""
        console.print(f"  Adding: [cyan]{stack_mg:.0f}mg[/cyan] at {local_stack}{fed_str}")
        console.print()

        current_combined = current_level(doses, cfg, streak=streak)
        new_combined = combined_concentration_at(0, hypothetical, cfg, now, streak=streak)

        console.print(f"  Current level:       {current_combined:.1f}% of peak")
        console.print(f"  After stack (now):   {new_combined:.1f}% of peak")

        peak_level = 0
        peak_time = 0
        for h in range(0, 24):
            lvl = combined_concentration_at(h, hypothetical, cfg, now, streak=streak)
            if lvl > peak_level:
                peak_level = lvl
                peak_time = h

        console.print(f"  Combined peak:       {peak_level:.1f}% at +{peak_time:.1f}h")

        parts = cfg.sleep_time.split(":")
        sleep_hour, sleep_min = int(parts[0]), int(parts[1])
        sleep_target = local_now.replace(hour=sleep_hour, minute=sleep_min, second=0, microsecond=0)
        if sleep_target <= local_now:
            sleep_target += timedelta(days=1)

        sleep_utc = sleep_target.astimezone(timezone.utc)
        sleep_level = level_at_time(sleep_utc, hypothetical, cfg, streak=streak)
        style = color_for_level(sleep_level, cfg)
        console.print(f"  Level at {cfg.sleep_time}:   [{style}]{sleep_level:.1f}%[/{style}] of peak")

    else:
        console.print(f"[red]Unknown calculator mode: {mode}[/red]")
        console.print("Available modes: sleep, dose, clear, stack")
        console.print("Try: stim calc sleep 22:00")


# ─── Commands: Config ─────────────────────────────────────────────────────

CONFIG_FIELDS = {
    "default_dose": "default_dose_mg",
    "half_life": "half_life_hours",
    "tmax": "tmax_hours",
    "sleep_time": "sleep_time",
    "sensitivity": "sleep_sensitivity",
    "late_cutoff": "late_dose_cutoff",
    "streak_warn": "streak_warn_days",
    "streak_alert": "streak_alert_days",
    "body_weight": "body_weight_kg",
    "steady_state": "steady_state_correction",
}

CONFIG_HELP = """[bold]View or change configuration.[/bold]

[bold cyan]Examples:[/bold cyan]
  stim config                      Show all settings
  stim config sleep_time           View one setting
  stim config sleep_time 23:45     Update sleep time
  stim config sensitivity high     Set sleep threshold sensitivity
  stim config body_weight 88       Set body weight (kg)
  stim config body_weight none     Remove weight correction
  stim config steady_state off     Disable accumulation correction

[bold cyan]Available keys:[/bold cyan]
  default_dose, half_life, tmax, sleep_time, sensitivity,
  late_cutoff, streak_warn, streak_alert, body_weight, steady_state
"""


@app.command(name="config", help=CONFIG_HELP)
def config_cmd(
    key: Optional[str] = typer.Argument(None, help="Config key to set (e.g. sleep_time)."),
    value: Optional[str] = typer.Argument(None, help="New value."),
    list_keys: bool = typer.Option(False, "--list", "-l", help="List all config keys."),
) -> None:
    """View or change configuration."""
    global cfg

    if list_keys or (key is None and value is None):
        table = Table(title="Config", box=box.ROUNDED)
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="green")
        table.add_column("Field", style="dim")

        for display_key, field_name in CONFIG_FIELDS.items():
            val = getattr(cfg, field_name)
            table.add_row(display_key, str(val) if val is not None else "[dim]not set[/dim]", field_name)

        console.print(table)
        console.print("\n[dim]Edit: stim config <key> <value>[/dim]")
        return

    if key is None:
        console.print("[red]Provide a key to set.[/red]")
        return

    if value is None:
        if key not in CONFIG_FIELDS:
            console.print(f"[red]Unknown key: {key}[/red]")
            console.print(f"Available: {', '.join(CONFIG_FIELDS.keys())}")
            return
        field_name = CONFIG_FIELDS[key]
        console.print(f"{key} = [cyan]{getattr(cfg, field_name)}[/cyan]")
        return

    if key not in CONFIG_FIELDS:
        console.print(f"[red]Unknown key: {key}[/red]")
        console.print(f"Available: {', '.join(CONFIG_FIELDS.keys())}")
        return

    field_name = CONFIG_FIELDS[key]
    field_type = type(getattr(cfg, field_name))

    try:
        if field_name == "body_weight_kg":
            new_val = None if value.lower() == "none" else float(value)
        elif field_type == int:
            new_val = int(value)
        elif field_type == float:
            new_val = float(value)
        else:
            new_val = value

        if field_name == "sleep_sensitivity" and new_val not in ("low", "medium", "high"):
            console.print("[red]Sensitivity must be: low, medium, or high[/red]")
            return

        if field_name == "steady_state_correction" and new_val not in ("auto", "on", "off"):
            console.print("[red]Steady state must be: auto, on, or off[/red]")
            return

        setattr(cfg, field_name, new_val)
        save_config(cfg)
        console.print(f"[green]✓[/green] {key} = [cyan]{new_val}[/cyan]")

    except ValueError:
        console.print(f"[red]Invalid value for {key}: {value}[/red]")


if __name__ == "__main__":
    app()
