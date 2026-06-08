# AGENTS.md

Instructions for AI agents working on `stim`.

## What This Is

A personal CLI for tracking armodafinil usage — log doses, estimate blood concentration, visualise patterns, and run pharmacokinetic calculators. All data stored locally in SQLite.

**Language:** Python 3.10+
**Package manager:** uv
**CLI framework:** Typer + Rich
**Database:** SQLite at `~/.stim/stim.db`
**Config:** TOML at `~/.stim/config.toml`
**Graphs:** plotext (terminal-rendered)

## Quick Start

```bash
# Install globally
cd /path/to/stim
uv tool install .

# Then use anywhere
stim --help
stim log 150mg
stim status

# Dev mode (run without installing)
uv run stim <command>

# Run tests
uv run pytest tests/ -v
```

## File Structure

```
stim/
├── pyproject.toml            # Project metadata, dependencies, entry point
├── uv.lock                   # Locked dependencies
├── AGENTS.md                 # This file
├── README.md                 # User-facing documentation
├── skills/
│   └── stim/
│       └── SKILL.md          # AI agent skill for stim
├── src/
│   └── stim/
│       ├── __init__.py       # Version (__version__)
│       ├── cli.py            # All CLI commands (Typer app)
│       ├── config.py         # Config dataclass, load/save, derived PK constants
│       ├── db.py             # SQLite connection, schema, migrations
│       ├── display.py        # Rich helpers, plotext graphs, color logic
│       └── pharmaco.py       # PK model: concentration, superposition, corrections
└── tests/
    ├── test_config.py        # Config defaults, derived values, sensitivity thresholds
    ├── test_display.py       # Colors, bars, sparklines, parsing, formatting
    ├── test_pharmaco.py      # Core PK math: concentration, cmax, superposition, clearance
    └── test_pk_features.py   # Food effect, steady-state, body weight, Vd update
```

## Installing the Skill

The `skills/stim/` directory is an AI agent skill. Install it so agents can discover and use stim:

```bash
# From the stim project root
npx skills add . -g -y
```

This symlinks to `~/.agents/skills/stim/` and `~/.pi/agent/skills/stim/`.

**When updating the skill:**
1. Edit `skills/stim/SKILL.md`
2. Run `npx skills add . -g -y` to reinstall
3. Commit both the skill source and any other changes

## Command Reference

### Logging

| Command | Purpose |
|---------|---------|
| `stim log 150mg` | Log dose now |
| `stim log 75mg 08:30` | Log with specific time today |
| `stim log 150mg yesterday` | Log for yesterday at current time |
| `stim log 150mg "yesterday 07:00"` | Log for yesterday at specific time |
| `stim log 150mg --fed` | Log with food (delays absorption ~2.5h) |
| `stim log 150mg --note "reason"` | Log with a note |
| `stim off` | Mark today as intentional off day |
| `stim undo` | Remove most recent entry (with confirmation) |

### Viewing

| Command | Purpose |
|---------|---------|
| `stim today` | Today's summary: doses, blood level, streak |
| `stim history` | Full dose history table |
| `stim history --days 30` | Last 30 days only |
| `stim history --graph` | Dose amounts over time as bar chart |
| `stim status` | Safety snapshot: streak, weekly summary, level, warnings |
| `stim blood` | Current estimated blood concentration + sparkline |
| `stim blood --graph` | Concentration curve (24h past, 24h future) |
| `stim blood -g --forward 48` | Show 48h into the future |
| `stim blood --table` | Hour-by-hour breakdown with visual bars |
| `stim blood --table -d 48` | Hour-by-hour for next 48 hours |
| `stim stats` | Frequency + time distribution + calendar grid |
| `stim stats --week` | This week only |

### Calculators

| Command | Purpose |
|---------|---------|
| `stim calc sleep 22:00` | Safe to sleep at 10pm? When is the safe time? |
| `stim calc dose 06:00` | When to dose if waking at 6am |
| `stim calc clear` | When will level drop below 10% of peak |
| `stim calc stack 75mg` | Model adding a dose right now |
| `stim calc stack 75mg 14:00` | Model adding a dose at 2pm |

### Config

| Command | Purpose |
|---------|---------|
| `stim config` | Show all settings |
| `stim config sleep_time 23:45` | Update sleep time |
| `stim config body_weight 88` | Set body weight (kg) |
| `stim config sensitivity high` | Set sleep threshold sensitivity |
| `stim config steady_state off` | Disable accumulation correction |

## PK Model

One-compartment oral absorption model with superposition for multiple doses.

### Core Parameters

| Parameter | Value | Source |
|---|---|---|
| F (bioavailability) | 0.73 | FDA label |
| Vd (volume of distribution) | 45 L | Willavize 2017 |
| ka (absorption rate) | ~1.2 hr⁻¹ | Back-calculated from Tmax=2h |
| ke (elimination rate) | ln(2)/t½ ≈ 0.0462 hr⁻¹ | Derived |
| Default half-life | 15 h | FDA label, Darwish 2009 |
| Tmax (fasted) | ~2 h | FDA label |
| Fed Tmax shift | +2.5 h | FDA label (range 2–4h) |

### How Concentration Is Calculated

```python
# One-compartment oral absorption
C(t) = (F × D × ka) / (Vd × (ka - ke)) × (e^(-ke × t) - e^(-ka × t))

# Relative concentration (% of peak)
C_relative(t) = C(t) / C(Tmax) × 100

# Multiple doses: superposition
C_total(t) = Σ C_i(t - t_i) for each dose i

# Corrections applied after superposition
C_display = C_total × weight_factor × steady_state_multiplier
```

### Steady-State Accumulation

Daily dosing accumulates beyond single-dose predictions (Lang 2025, Darwish 2009):

| Streak | Multiplier (auto mode) | Source |
|--------|----------------------|--------|
| 1–3 days | 1.0× | Single-dose model accurate |
| 4–6 days | 1.0× | Auto mode doesn't apply yet |
| 7+ days | 1.5× | Lang 2025 |
| 14+ days | 1.7× | Darwish 2009 |

### Body Weight Correction

Optional. Reference: 70 kg. Exponent: 0.47 (Willavize 2017).

```python
factor = (70 / weight_kg) ** 0.47
# 50 kg → 1.164 (16.4% higher AUC)
# 70 kg → 1.000
# 88 kg → 0.898
# 150 kg → 0.709
```

### Food Effect

`--fed` flag adds 2.5h Tlag to absorption for that dose. Total AUC unchanged, peak delayed and flattened. Significant for sleep calculations.

## Config Fields

| Key | Field | Default | Description |
|---|---|---|---|
| `default_dose` | `default_dose_mg` | 150 | Default dose for calculators |
| `half_life` | `half_life_hours` | 15 | Elimination half-life (range: 10–15h) |
| `tmax` | `tmax_hours` | 2 | Time to peak concentration |
| `sleep_time` | `sleep_time` | 22:00 | Target sleep time for warnings |
| `sensitivity` | `sleep_sensitivity` | medium | low=35%, medium=25%, high=15% |
| `late_cutoff` | `late_dose_cutoff` | 13:00 | Warn when dosing after this time |
| `streak_warn` | `streak_warn_days` | 3 | Yellow warning streak |
| `streak_alert` | `streak_alert_days` | 5 | Red alert streak |
| `body_weight` | `body_weight_kg` | not set | Body weight for PK correction |
| `steady_state` | `steady_state_correction` | auto | Accumulation: auto/on/off |

## Gotchas

1. **Blood level is % of peak, not absolute mg/L.** This is body-weight independent by design. Don't try to convert to mg/L — the model deliberately avoids requiring precise Vd/F values for the display.

2. **`--fed` flag is per-dose, not global.** It shifts absorption by 2.5h for that specific dose only. Other doses in the same log session are unaffected.

3. **`stim undo` removes the most recent entry.** There is no `stim remove <id>`. For targeted deletion, use SQLite directly: `sqlite3 ~/.stim/stim.db "DELETE FROM doses WHERE id = N;"`

4. **`stim off` creates a zero-dose marker.** It resets streak counting but does not affect blood level calculations. The marker has `is_off_day = 1` and `amount_mg = 0`.

5. **Steady-state in auto mode only kicks in at streak ≥ 7.** Days 4–6 are not corrected. Use `stim config steady_state on` to force correction earlier.

6. **Config changes take effect immediately.** The config is re-read on every command invocation. No restart needed.

7. **All datetime storage is UTC.** Local time conversion happens only at display time via `datetime.astimezone()`.

8. **`stim today` uses local time boundaries.** Your "today" starts at midnight your time, not UTC midnight.

9. **Times display in 12-hour AM/PM format.** "8:15 AM", "12:30 PM" — not 24-hour.

10. **ka is back-calculated from Tmax, not from population PK.** The population PK model (Willavize 2017) gives ka=3.07 but with a Tlag. We use ka≈1.2 which implicitly absorbs the lag time and correctly produces Tmax=2h.

11. **Windows encoding.** The CLI forces UTF-8 output to handle Unicode characters (checkmarks, blocks) on Windows cp1252 terminals.

12. **Graph time ranges.** `stim blood --graph` defaults to 24h back, 24h forward. Use `--back` and `--forward` to customise. `stim blood --table` defaults to 24h forward, customise with `-d`.

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
CREATE INDEX idx_doses_taken_at ON doses(taken_at);
```

Migration: the `fed` column is added automatically if missing (ALTER TABLE).

Direct access:
```bash
sqlite3 ~/.stim/stim.db "SELECT * FROM doses ORDER BY id DESC LIMIT 10;"
sqlite3 ~/.stim/stim.db "SELECT date(taken_at), COUNT(*), SUM(amount_mg) FROM doses GROUP BY date(taken_at);"
```

## Common Tasks

### Adding a New Command

1. Add the command function in `src/stim/cli.py`
2. Use `@app.command()` decorator
3. Use existing helpers: `get_doses()`, `compute_streak()`, `check_sleep_warning()`
4. Use Rich for display: `console.print()`, `Panel`, `Table`
5. Add tests in `tests/`
6. Run `uv run pytest tests/ -v` to verify

### Modifying the PK Model

1. Edit `src/stim/pharmaco.py`
2. Key functions: `concentration_at()`, `combined_concentration_at()`, `current_level()`
3. Update `src/stim/config.py` if adding new parameters
4. Update tests in `tests/test_pharmaco.py` and `tests/test_pk_features.py`
5. Run `uv run pytest tests/ -v`

### Updating Config

1. Add field to `Config` dataclass in `src/stim/config.py`
2. Add to `CONFIG_FIELDS` dict in `src/stim/cli.py` (display name → field name)
3. Update `stim config` command if needed
4. Add tests in `tests/test_config.py`

### Updating the Skill

1. Edit `skills/stim/SKILL.md`
2. Run `npx skills add . -g -y` to reinstall
3. Commit changes

### Debugging

```bash
# See raw config
cat ~/.stim/config.toml

# Check database
sqlite3 ~/.stim/stim.db ".schema"
sqlite3 ~/.stim/stim.db "SELECT COUNT(*) FROM doses;"

# Test PK calculation directly
uv run python -c "
from stim.config import Config
from stim.pharmaco import concentration_at, cmax, relative_concentration_at
cfg = Config()
print(f'ke={cfg.ke}, ka={cfg.ka}')
print(f'Cmax 150mg: {cmax(150, cfg):.4f}')
print(f'Level at 4h: {relative_concentration_at(4, 150, cfg):.1f}%')
"

# Run specific test file
uv run pytest tests/test_pharmaco.py -v

# Run specific test
uv run pytest tests/test_pharmaco.py::TestConcentrationAt::test_peak_at_tmax -v
```

## Testing

```bash
# All tests
uv run pytest tests/ -v

# Specific file
uv run pytest tests/test_pharmaco.py -v

# With coverage
uv run pytest tests/ --tb=short
```

**Test count:** 119 tests across 4 files:
- `test_config.py` — 17 tests (defaults, derived values, sensitivity)
- `test_display.py` — 27 tests (colors, bars, sparklines, parsing)
- `test_pharmaco.py` — 47 tests (core PK math, superposition, clearance)
- `test_pk_features.py` — 28 tests (food effect, steady-state, body weight)

## PR/Commit Conventions

- Commit messages: lowercase, describe what changed
- Run `uv run pytest tests/ -v` before committing
- Update `skills/stim/SKILL.md` if commands or config change
- Run `npx skills add . -g -y` after skill changes
- Don't commit `.venv/`, `__pycache__/`, `*.db`, or `~/.stim/` contents

## Source Studies

| Study | Key contribution |
|---|---|
| Willavize SA et al. (2017). *Population PK Modeling of Armodafinil.* J Clin Pharmacol. | CL/F=2.01 L/h, Vc/F=45 L, absorption t½=0.226h, weight covariate |
| Darwish M et al. (2009). *PK Profile of Armodafinil.* Clin Drug Investig. | Linear PK 50–400mg, steady state in 7 days, AUC 1.8× at steady state |
| Darwish M et al. (2009). *Comparison of late-day concentrations.* Clin Drug Investig. | 44% higher late-day levels vs modafinil, 28–42% less fluctuation |
| Lang J et al. (2025). *PK and Safety in Chinese Healthy Humans.* Clin Pharmacol Drug Dev. | Accumulation: 1.5× parent, 1.3× R-modafinil acid, 7.8× sulfone |
| FDA label — NUVIGIL (armodafinil) Tablets | Tmax ~2h fasted, food delays 2–4h, Vd ~42L, t½ ~15h |
