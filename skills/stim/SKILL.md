---
name: stim
description: >
  Track armodafinil usage from the terminal — log doses, view blood concentration
  estimates, check sleep safety, and run pharmacokinetic calculators. Use when the
  user mentions stim, armodafinil, modafinil, dose tracking, blood levels, sleep
  timing with stimulants, or pharmacokinetic calculations.
compatibility: Requires Python 3.10+ and uv. Install with `uv tool install .` from the stim project root.
metadata:
  author: sonny
  version: "0.2.0"
  pk-model: one-compartment oral absorption (Willavize 2017, Darwish 2009, Lang 2025)
allowed-tools: Bash(uv:*)
---

# stim — Armodafinil Usage Tracker

A personal CLI for logging armodafinil doses, estimating blood concentration,
visualising usage patterns, and running safety calculators. All data stored
locally in SQLite at `~/.stim/stim.db`. Config at `~/.stim/config.toml`.

## Quick reference

```
stim log 150mg                     # log dose now
stim log 75mg 08:30                # log with specific time
stim log 150mg --fed               # log with food (delays absorption ~2.5h)
stim log 150mg --note "felt tired" # log with note
stim off                           # mark today as off day
stim undo                          # remove most recent entry

stim today                         # today's summary
stim history                       # all doses table
stim history --days 30             # last 30 days
stim history --graph               # dose chart over time
stim status                        # safety snapshot (streak, level, warnings)
stim blood                         # current blood level estimate
stim blood --graph                 # concentration curve (24h past, 24h future)
stim blood -g --forward 48         # show 48h into the future
stim blood --table                 # hour-by-hour breakdown
stim blood --table -d 48           # hour-by-hour for next 48h
stim stats                         # frequency + time distribution graphs
stim stats --week                  # this week only

stim calc sleep 22:00              # safe to sleep at 10pm?
stim calc dose 06:00               # when to dose if waking at 6am
stim calc clear                    # when will level drop below 10%
stim calc stack 75mg               # model adding a dose now
stim calc stack 75mg 14:00         # model adding a dose at 2pm

stim config                        # show all settings
stim config sleep_time 23:45       # update a setting
stim config body_weight 88         # set body weight for PK correction
```

## Installation

```bash
cd /path/to/stim
uv tool install .
```

Or run directly during development:

```bash
uv run stim <command>
```

## PK model

One-compartment oral absorption model with superposition for multiple doses.

| Parameter | Value | Source |
|---|---|---|
| F (bioavailability) | 0.73 | FDA label |
| Vd | 45 L | Willavize 2017 |
| ka (absorption rate) | ~1.2 hr⁻¹ | Back-calculated from Tmax=2h |
| ke (elimination rate) | ln(2)/t½ ≈ 0.0462 hr⁻¹ | Derived from half-life |
| Default half-life | 15 h | FDA label, Darwish 2009 |
| Tmax (fasted) | ~2 h | FDA label |
| Fed Tmax shift | +2.5 h | FDA label (range 2–4h) |

### Steady-state accumulation

Daily dosing accumulates beyond single-dose model predictions:

- Streak 1–3 days: 1.0× (single-dose model accurate)
- Streak 4–6 days: 1.0× in auto mode
- Streak 7+ days: 1.5× (Lang 2025)
- Streak 14+ days: 1.7× (Darwish 2009)

Set `stim config steady_state on` to force correction at any streak.
Set `stim config steady_state off` to disable entirely.

### Body weight correction

Optional. Reference weight: 70 kg. Lighter users have higher AUC per mg,
heavier users lower. Based on Willavize 2017 population PK (exponent 0.47).

```bash
stim config body_weight 88    # set weight in kg
stim config body_weight none  # remove correction
```

### Food effect

Use `--fed` flag when logging. Adds 2.5h Tlag to absorption curve for that dose.
Total AUC unchanged, but peak is delayed and flattened. Significant for sleep
calculations — a fed dose at 08:00 peaks around 12:30 instead of 10:00.

## Display conventions

- **Green**: safe, within normal range
- **Yellow**: caution (streak 3+ days, or level between 15% and sleep threshold)
- **Red**: alert (streak 5+ days, or level above sleep threshold)
- All times displayed in 12-hour AM/PM format in local time; stored as UTC ISO 8601
- Dose amounts always with "mg" suffix

## Config fields

| Key | Field | Default | Description |
|---|---|---|---|
| `default_dose` | `default_dose_mg` | 150 | Default dose for calculators |
| `half_life` | `half_life_hours` | 15 | Elimination half-life (range: 10–15h) |
| `tmax` | `tmax_hours` | 2 | Time to peak concentration |
| `sleep_time` | `sleep_time` | 22:00 | Target sleep time for warnings |
| `sensitivity` | `sleep_sensitivity` | medium | Sleep threshold: low=35%, medium=25%, high=15% |
| `late_cutoff` | `late_dose_cutoff` | 13:00 | Warn when dosing after this time |
| `streak_warn` | `streak_warn_days` | 3 | Yellow warning streak length |
| `streak_alert` | `streak_alert_days` | 5 | Red alert streak length |
| `body_weight` | `body_weight_kg` | not set | Body weight for PK correction |
| `steady_state` | `steady_state_correction` | auto | Accumulation mode: auto/on/off |

## Safety warnings

The CLI automatically shows:

1. **Sleep impact warning** when logging a dose that will leave levels above threshold at bedtime
2. **Late dose warning** when logging after the configured cutoff (default 1:00 PM)
3. **Streak warning** (yellow) at 3+ consecutive days, **alert** (red) at 5+
4. **Metabolite note** at streak 5+ — modafinil sulfone accumulates 7.8× and isn't modelled
5. **CYP3A4 note** at streak 7+ — drug interaction disclaimer

## Gotchas

- Blood level is expressed as **% of a reference single-dose peak**, not absolute mg/L. This is body-weight independent and clinically useful for timing.
- The `--fed` flag shifts absorption by 2.5h for **that dose only**. Other doses in the same session are unaffected.
- `stim undo` removes the **most recent entry** with confirmation. There is no `stim remove` by ID (use SQLite directly for that).
- `stim off` creates a zero-dose marker that resets streak counting. It does not affect blood level calculations.
- The steady-state multiplier in `auto` mode only kicks in at streak ≥ 7. Days 4–6 are not corrected in auto mode.
- The sparkline in `stim blood` shows the last 24 hours of the **modelled curve**, not actual measurements.
- Config changes take effect immediately — no restart needed. The config is re-read on every command.
- All datetime arithmetic uses UTC internally. Local time conversion happens only at display time.
- `stim today` uses local time boundaries, not UTC midnight. Your "today" starts at midnight your time.
- Times display in 12-hour AM/PM format (e.g. "8:15 AM", "12:30 PM").

## Database

SQLite at `~/.stim/stim.db`. Schema:

```sql
CREATE TABLE doses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    amount_mg REAL NOT NULL,
    taken_at TEXT NOT NULL,          -- UTC ISO 8601
    note TEXT,
    is_off_day INTEGER DEFAULT 0,
    fed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
```

To query directly:

```bash
sqlite3 ~/.stim/stim.db "SELECT * FROM doses ORDER BY id DESC LIMIT 10;"
```

## Running tests

```bash
uv run pytest tests/ -v
```

119 tests covering PK math, config, display helpers, food effect, steady-state accumulation, and body weight correction.

## Source studies

- Willavize SA et al. (2017). *Population PK Modeling of Armodafinil.* J Clin Pharmacol. — CL/F, Vc/F, weight covariate
- Darwish M et al. (2009). *PK Profile of Armodafinil.* Clin Drug Investig. — Linear PK, steady state in 7 days, food effect
- Darwish M et al. (2009). *Comparison of late-day concentrations.* Clin Drug Investig. — 44% higher late-day levels vs modafinil
- Lang J et al. (2025). *PK and Safety in Chinese Healthy Humans.* Clin Pharmacol Drug Dev. — Accumulation ratios: 1.5× parent, 7.8× sulfone
- FDA label — NUVIGIL (armodafinil) Tablets
