# stim

A terminal CLI for tracking armodafinil usage — log doses, estimate blood concentration, check sleep safety, and run pharmacokinetic calculators. All data stored locally in SQLite.

Built with Python, Typer, Rich, and plotext.

## Install

Requires [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/SonnyTaylor/stim.git
cd stim
uv tool install .
```

Verify:
```bash
stim --version
```

## Quick start

```bash
# Log a dose
stim log 150mg
stim log 75mg 08:30
stim log 150mg --fed --note "with breakfast"

# Check your status
stim today
stim status
stim blood

# See history
stim history
stim history --days 14

# Mark an off day
stim off
```

## Blood level tracking

`stim` uses a one-compartment pharmacokinetic model to estimate armodafinil plasma concentration as a percentage of peak. The model accounts for:

- **Absorption and elimination** — standard one-compartment oral absorption with F=0.73, Vd=45L, ka≈1.2 hr⁻¹, ke derived from configurable half-life (default 15h)
- **Multiple doses** — superposition principle sums individual dose curves
- **Food effect** — `--fed` flag adds 2.5h absorption lag per dose
- **Steady-state accumulation** — auto-applies 1.5× correction at 7+ consecutive days (Lang 2025)
- **Body weight correction** — optional, based on Willavize 2017 population PK

```bash
# Current blood level
stim blood

# Full 48h concentration curve
stim blood --graph
```

## Sleep safety

`stim` warns when a dose will leave levels above your sleep threshold at bedtime.

```bash
# Will I sleep ok if I dose now?
stim calc sleep 23:45

# When should I dose if I wake at 6am?
stim calc dose 06:00

# When will my level drop below 10%?
stim calc clear

# Model adding another dose
stim calc stack 75mg
```

Thresholds by sensitivity:
| Setting | Threshold | Use when |
|---------|-----------|----------|
| `low` | 35% of peak | You sleep fine on armodafinil |
| `medium` | 25% of peak | Default — some disruption possible |
| `high` | 15% of peak | Sensitive to stimulants before bed |

## Stats and graphs

```bash
# Full stats with frequency chart, time distribution, and calendar grid
stim stats

# This week only
stim stats --week
```

## Safety tracking

**Streak detection:**
- Yellow warning at 3+ consecutive days
- Red alert at 5+ consecutive days
- `stim off` resets the streak

**Automatic warnings:**
- Sleep impact warning when logging a dose that disrupts sleep
- Late dose warning when dosing after cutoff (default 1:00 PM)
- Metabolite accumulation note at 5+ day streak
- CYP3A4 interaction note at 7+ day streak

## Configuration

```bash
# View all settings
stim config

# Update settings
stim config sleep_time 23:45
stim config sensitivity high
stim config body_weight 88
stim config late_cutoff 14:00
stim config half_life 12
stim config steady_state off
```

| Key | Default | Description |
|-----|---------|-------------|
| `default_dose` | 150mg | Default dose for calculators |
| `half_life` | 15h | Your elimination half-life (range: 10–15h) |
| `tmax` | 2h | Time to peak concentration |
| `sleep_time` | 22:00 | Target sleep time |
| `sensitivity` | medium | Sleep threshold: low/medium/high |
| `late_cutoff` | 13:00 | Warn when dosing after this time |
| `streak_warn` | 3 | Yellow warning at N days |
| `streak_alert` | 5 | Red alert at N days |
| `body_weight` | not set | Weight in kg for PK correction |
| `steady_state` | auto | Accumulation correction: auto/on/off |

## Pharmacokinetic model

The concentration at time `t` after a single dose:

```
C(t) = (F × D × ka) / (Vd × (ka − ke)) × (e^(−ke × t) − e^(−ka × t))
```

Where:
- `F` = 0.73 (bioavailability)
- `D` = dose in mg
- `Vd` = 45 L (Willavize 2017)
- `ka` ≈ 1.2 hr⁻¹ (absorption rate, back-calculated from observed Tmax)
- `ke` = ln(2) / t½ (elimination rate)

Display is normalised to % of individual dose peak — body-weight independent, honest, and clinically useful for timing decisions.

### Sources

| Study | Key findings |
|-------|-------------|
| Willavize et al. (2017). *Population PK Modeling of Armodafinil.* J Clin Pharmacol. | CL/F, Vc/F=45L, weight covariate |
| Darwish et al. (2009). *PK Profile of Armodafinil.* Clin Drug Investig. | Linear PK, steady state in 7 days |
| Darwish et al. (2009). *Late-day concentrations comparison.* Clin Drug Investig. | 44% higher evening levels vs modafinil |
| Lang et al. (2025). *PK in Chinese Healthy Humans.* Clin Pharmacol Drug Dev. | 1.5× parent accumulation, 7.8× sulfone |
| FDA label — NUVIGIL (armodafinil) | Tmax ~2h, food delays 2–4h, t½ ~15h |

## Data

All data stored locally at `~/.stim/stim.db` (SQLite). Config at `~/.stim/config.toml`.

```bash
# Direct database access
sqlite3 ~/.stim/stim.db "SELECT * FROM doses ORDER BY id DESC LIMIT 10;"
```

## Development

```bash
# Install in dev mode
uv pip install -e .

# Run tests (119 tests)
uv run pytest tests/ -v

# Run specific test file
uv run pytest tests/test_pharmaco.py -v

# Run CLI without installing
uv run stim --help
```

## License

MIT
