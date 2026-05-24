<p align="center">
  <h1 align="center">NQ-ES Trader</h1>
  <p align="center">
    Autonomous MNQ futures day-trading system for TopStepX funded accounts.<br>
    12 quantitative models · Model-tiered risk · Monte Carlo validated · Bias-free simulation
  </p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-3776ab?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Models-12-22c55e?style=flat-square" alt="Models">
  <img src="https://img.shields.io/badge/Account-TopStepX%20100K-f59e0b?style=flat-square" alt="Account">
  <img src="https://img.shields.io/badge/Eval%20Pass-93.7%25-3b82f6?style=flat-square" alt="Eval Pass Rate">
  <img src="https://img.shields.io/badge/Profit%20Factor-1.66-10b981?style=flat-square" alt="Profit Factor">
</p>

---

## Overview

This system trades MNQ (Micro E-mini Nasdaq-100) futures on TopStepX 100K funded accounts. It runs 12 independent quantitative models during regular trading hours (9:30 AM–4:00 PM ET), each generating signals based on different market microstructure conditions.

All models share identical exit mechanics (trail stops, breakeven, time stops). A priority-based conflict resolver ensures only one trade is active at any time. Risk is sized per-model based on historical edge.

**Backtest and live execution share the same configuration.** The backtest engine simulates fills on the next bar's open (not the signal bar's close) to eliminate look-ahead bias.

---

## Performance

### Backtest Results (Dec 2022 – May 2026)

| Metric | Value |
|:--|--:|
| Total Trades | 2,716 |
| Trading Days | 792 |
| Trades per Day | 3.4 |
| Win Rate | 44.6% |
| Avg Win / Avg Loss | +1.34R / -0.65R |
| Expectancy | **+0.238R per trade** |
| Profit Factor | **1.66** |
| Total R | **+647.5R** |
| Max Drawdown | -12.6R |

### Monte Carlo Validation (25,000 simulations)

| Metric | Value |
|:--|--:|
| Eval Pass Rate ($6K target, $3K trailing DD) | **93.7%** |
| Median Days to Pass | 15 |
| Funded Survival (60 days) | 97.2% |
| P($5K+ extraction) | 92.4% |
| P($10K+ extraction) | 81.5% |
| Avg 60-Day Extraction | $14,276 |

### Walk-Forward Validation (Out-of-Sample)

Each year tested using only data from that year, with prior years providing regime context:

| Year | P($10K) | Survival | Avg Extraction |
|:--|--:|--:|--:|
| 2023 | 70.0% | 88.8% | $12,173 |
| 2024 | 70.3% | 89.1% | $12,152 |
| 2025 | 89.2% | 95.6% | $15,980 |
| 2026 (Jan–Apr) | 97.7% | 98.2% | $23,896 |

### 2026 Out-of-Sample Monthly Returns

| Month | R-Multiple | Trades | Win Rate |
|:--|--:|--:|--:|
| January | +24.5R | 48 | 58% |
| February | +22.5R | 61 | 44% |
| March | +29.2R | 57 | 46% |
| April | +23.6R | 63 | 46% |
| **Total** | **+99.8R** | **229** | **48%** |

---

## Charts

| | |
|:--:|:--:|
| ![Equity Curve](output/charts/chart_equity_drawdown.png) | ![Model Breakdown](output/charts/chart_model_breakdown.png) |
| Equity Curve and Drawdown | Per-Model Performance |
| ![Monthly Yearly](output/charts/chart_monthly_yearly.png) | ![Monte Carlo](output/charts/chart_funded_mc.png) |
| Monthly and Yearly Returns | Monte Carlo Funded Simulation |
| ![Timing Analysis](output/charts/chart_timing_analysis.png) | ![Walk Forward](output/charts/chart_walkforward.png) |
| Timing and Distribution | Walk-Forward Validation |

---

## Backtesting Methodology

The simulation engine uses institutional-standard practices to avoid common backtesting pitfalls:

| Practice | Implementation |
|:--|:--|
| **No look-ahead bias** | Entry fills at the next bar's open after signal generation, not the signal bar's close |
| **Gap filter** | Trades where the open gaps past the stop are discarded (dead-on-arrival) |
| **Risk re-validation** | After computing actual fill price, risk is re-checked against model's min/max tick range and minimum reward-to-risk ratio |
| **Slippage model** | 0.25 tick adverse slippage applied to all non-target exits (stops, time stops, session close) |
| **No partial fills** | Full position assumed filled or not filled |
| **Single position** | No overlapping trades; one active position at a time |
| **Daily controls** | Win cap (2.0R), dollar loss cap ($1,000), consecutive loss cooldown (10) |
| **Session boundaries** | No overnight holds; all positions flattened by 4:00 PM ET |

---

## Quick Start

### Prerequisites

- Python 3.10 or higher
- Git

### 1. Clone and Install

```bash
git clone https://github.com/s-k-28/nq-es-trader-5k-payout.git
cd nq-es-trader-5k-payout
pip install -r requirements.txt
```

Dependencies: `pandas`, `numpy`, `matplotlib`, `tabulate`, `requests`, `python-dotenv`.

Verify installation:
```bash
python3 -c "from config import Config; print('Setup OK')"
```

### 2. Run Backtest

```bash
python3 run_multi.py --nq data/Dataset_NQ_1min_2022_2025.csv
```

With 2026 out-of-sample data:

```bash
python3 run_multi.py --nq data/mnq_2026_1min.csv --history data/Dataset_NQ_1min_2022_2025.csv
```

### 3. Generate Charts and Monte Carlo

```bash
python3 scripts/generate_charts.py
```

Takes 1–2 minutes. Produces 6 chart PNGs in `output/charts/`, runs 25,000 eval pass simulations and 25,000 funded account simulations.

### 4. Launch Dashboard

```bash
python3 frontend/server.py
```

Open **http://localhost:8080**. Four tabs: Interactive Charts, Deep Analysis, Monte Carlo, Strategy Rules.

---

## Strategy Architecture

### 12-Model Ensemble

| # | Model | Type | Priority | Risk/Trade | Edge |
|:--|:--|:--|--:|--:|:--|
| 1 | OU Reversion | Mean-reversion | 15 | $2,500 | Ornstein-Uhlenbeck z-score on price-VWAP deviation. Hurst < 0.45. Quality >= 4. |
| 2 | OU Lunch Zone | Mean-reversion | 16 | $400 | Dedicated 11:30–13:30 ET lunch fade. Tighter z-score during low-volume window. |
| 3 | PD Level Reversion | Mean-reversion | 22 | $900 | Fades at previous-day high/low with reversal candle confirmation. |
| 4 | VWAP Reversion | Mean-reversion | 25 | $400 | Bidirectional VWAP standard-deviation fade. Targets snap to session VWAP. |
| 5 | VWAP Band Scalper | Mean-reversion | 25 | $400 | Faster VWAP/OU band fade with partial-move-to-VWAP target. |
| 6 | Opening Range Rev | Mean-reversion | 28 | $400 | Fades extended moves beyond 15-min opening range. Window: 10:00–12:00 ET. |
| 7 | EMA Reversion | Mean-reversion | 30 | $400 | Fades 2.5+ std-dev extensions from 20-period EMA. Window: 9:50–14:30 ET. |
| 8 | Sweep Reversal | Mean-reversion | 35 | $400 | Liquidity sweep at PDH/PDL/session extremes followed by immediate reversal. |
| 9 | Opening Drive | Momentum | 12 | $400 | Early RTH drive pullbacks toward VWAP after strong 9:30–9:35 push. |
| 10 | Kalman Momentum | Momentum | 40 | $400 | Kalman filter slope when Hurst >= 0.5. 5-bar slope consistency. 10:15–14:00 ET. |
| 11 | Trend Continuation | Momentum | 40 | $400 | EMA/regime trend following with pullback entries. |
| 12 | PM Momentum | Momentum | 50 | $400 | Afternoon session Kalman slope pullback. 13:30–15:00 ET. |

### Per-Model Results (2022–2026, bias-free)

| Model | Trades | Win Rate | Expectancy | Total R |
|:--|--:|--:|--:|--:|
| ou_rev | 598 | 48% | +0.293R | +175.2R |
| or_rev | 272 | 44% | +0.336R | +91.4R |
| trend | 257 | 47% | +0.267R | +68.5R |
| kalman_mom | 299 | 41% | +0.228R | +68.2R |
| pm_mom | 365 | 47% | +0.182R | +66.5R |
| vwap_scalp | 189 | 48% | +0.260R | +49.1R |
| ou_lunch | 252 | 40% | +0.157R | +39.6R |
| open_drive | 84 | 40% | +0.278R | +23.3R |
| ema_rev | 124 | 41% | +0.184R | +22.8R |
| pd_rev | 75 | 47% | +0.281R | +21.0R |
| vwap_rev | 150 | 39% | +0.128R | +19.2R |
| sweep | 51 | 37% | +0.050R | +2.5R |

### Signal Flow

```
1-min bars ──► compute_vwap() + compute_opening_range()
           ──► compute_all_quant_features() (OU, Hurst, Kalman, Parkinson, BB)
           ──► 12 models generate signals independently
           ──► filter: cut off after 3:30 PM ET
           ──► resolve conflicts: 3-bar cooldown, priority-based (lower # wins)
           ──► quality filter: OU needs Q >= 4, all others pass through
           ──► engine: fill on next bar open, simulate with BE / trail / time-stop
```

### Quantitative Features

Computed in `strategy/quant/features.py`:

| Feature | Method | Window |
|:--|:--|--:|
| Ornstein-Uhlenbeck | Rolling OLS on price-VWAP deviation. Estimates mean-reversion speed, half-life, z-score. | 60 bars |
| Hurst Exponent | Variance-ratio method (16-bar vs 1-bar returns). H < 0.45 = mean-reverting, H > 0.55 = trending. | 120 bars |
| Kalman Filter | 2x2 state-space model (level + slope). Inline matrix math for speed on 1M+ bars. | Rolling |
| Parkinson Volatility | High-low range estimator, more efficient than close-to-close. | 30 bars |
| Bollinger Band Squeeze | BBW percentile for volatility expansion detection. | 20 bars |

---

## Exit Mechanics

All 12 models share the same exit profile:

| Parameter | Value | Description |
|:--|:--|:--|
| Trail stop | 0.1% of MFE | Once MFE >= partial threshold, trailing stop activates. Trail IS the primary exit. |
| Breakeven | 0.6R trigger | Stop moves to entry once trade moves 0.6x risk in your favor. |
| Partial profit | Model-configured | Locks partial R when MFE reaches partial_rr threshold. |
| Time stop | 30–45 min | Closes at market if trade has not hit breakeven within the model's time limit. |
| Session close | 3:55 PM ET | Flatten everything. No overnight positions. |

### Exit Distribution (Backtest)

| Exit Type | Count | % of Trades | Avg R |
|:--|--:|--:|--:|
| Trail | 1,799 | 66% | +0.87R |
| Stop | 893 | 33% | -1.00R |
| Target | 18 | <1% | +2.18R |
| Time/Session | 6 | <1% | -0.11R |

---

## Risk Management

### Daily Controls

| Control | Value | Purpose |
|:--|:--|:--|
| Daily win cap | 2.0R | Stop trading after a good day. Protect profits. |
| Dollar loss cap | $1,000/day | Hard stop matching TopStepX daily limit. |
| Consecutive cooldown | 10 losses | Skip next signal after 10 straight losses. Circuit breaker. |
| Max concurrent | 1 | One trade at a time. No overlapping positions. |

### Model-Tiered Sizing

| Tier | Models | Risk/Trade | Rationale |
|:--|:--|--:|:--|
| High | ou_rev | $2,500 | Highest edge (48% WR, +0.293R expectancy), quality-filtered |
| Medium | pd_rev | $900 | Strong at institutional levels (47% WR) |
| Standard | All others | $400 | Diversified coverage across market conditions |

**Contract formula:**

```
contracts = min(20, floor(risk_dollars / (risk_ticks × $0.50)))
```

### Risk Validation

Every signal is checked against hard floors and ceilings:

| Parameter | Value |
|:--|:--|
| Global min risk | 40 ticks (10 points) |
| Global max risk | 80 ticks (20 points) |
| Per-model min RR | 2.0x (configurable per model) |

Signals outside these bounds are discarded before entry.

---

## TopStepX Account Rules

### Evaluation Phase

| Rule | Value |
|:--|:--|
| Profit target | $6,000 |
| Trailing drawdown | $3,000 |
| Time limit | None |

**Monte Carlo result:** 93.7% pass rate (25K simulations). Median 15 trading days.

### Funded Account Phase

| Rule | Value |
|:--|:--|
| Starting balance | $100,000 |
| Trailing drawdown | $3,000 (trails upward with profit) |
| Static floor | Locks at $0 P&L when peak profit reaches $3,000 |
| Dollar loss cap | $1,000 per day |
| Max payout | $5,000 per withdrawal |
| Payout cap | 30% of current balance |
| Green day minimum | $150 profit |
| Green days per payout | 5 |

### Payout Mechanics

1. **Build to $3K peak** — Trade until peak profit hits $3,000. Drawdown floor locks at $0.
2. **Accumulate green days** — Every day with $150+ profit counts. Need 5 per payout.
3. **Withdraw** — After 5 green days, withdraw up to min($5,000, 30% of balance).
4. **Repeat** — Floor stays locked. Account is safe above $100,000.

---

## Live Trading

### Setup

```bash
cp .env.example .env
```

Configure `.env`:

```env
TOPSTEP_USER=your_topstep_username
TOPSTEP_API_KEY=your_api_key
TOPSTEP_ENV=demo
```

| Variable | Description |
|:--|:--|
| `TOPSTEP_USER` | Your TopStepX login username |
| `TOPSTEP_API_KEY` | API key from your TopStepX dashboard |
| `TOPSTEP_ENV` | `demo` for paper trading, `live` for funded account |

### Run

```bash
# Demo mode (paper trading, no real orders)
python3 run_live.py

# Live mode (real funded account)
python3 run_live.py --env live
```

The bot will:
1. Authenticate with TopStepX via REST API
2. Load ~83 days of 1-minute history for regime warmup
3. Detect the current front-month MNQ contract automatically
4. Begin trading all 12 models autonomously

**To stop:** Press `Ctrl+C`. Flattens all positions and cancels pending orders before exit.

### Live Executor Architecture

The live executor (`live/executor_multi.py`) implements:

- **Limit entry orders** with 60-second timeout (auto-cancel if not filled)
- **Bracket exits** (stop + target placed immediately after fill)
- **Trailing stop advancement** via broker stop modification
- **Breakeven move** with retry on failure
- **Session flatten** at 3:55 PM ET
- **Orphan position detection** on startup
- **Mid-session restart** with state reconstruction
- **Adaptive guard** for regime-aware position sizing
- **Telegram alerts** for all entries, exits, and risk events
- **Decision logging** with fsync for crash forensics

### Troubleshooting

| Problem | Solution |
|:--|:--|
| `Missing credentials` | Verify `.env` has `TOPSTEP_USER` and `TOPSTEP_API_KEY` |
| `Auth failed` | Re-check credentials on TopStepX dashboard |
| `Contract not found` | Quarterly rollover may be in progress; wait for new contract |
| `No active accounts` | Check account status on TopStepX dashboard |

---

## Project Structure

```
nq-es-trader-5k-payout/
├── config.py                          Configuration: strategy, risk, funded account rules
├── run_multi.py                       Backtest runner with per-model reporting
├── run_live.py                        Live bot entry point (TopStepX 100K)
├── run_paper.py                       Paper trading simulator (no account needed)
│
├── strategy/
│   ├── multi.py                       Signal orchestrator: generate, filter, resolve
│   ├── quality.py                     OU quality scoring (Q >= 4 filter)
│   ├── vwap.py                        Session VWAP + 15-min opening range
│   ├── quant/
│   │   └── features.py               OU, Hurst, Kalman, Parkinson, BB squeeze
│   └── models/
│       ├── __init__.py                ALL_MODELS registry (12 models)
│       ├── base.py                    BaseModel, Signal, ModelRiskProfile
│       ├── ou_reversion.py            OU mean-reversion (P15, $2,500)
│       ├── ou_lunch.py                Lunch-session OU (P16, $400)
│       ├── pd_level_reversion.py      Previous-day level fade (P22, $900)
│       ├── vwap_reversion.py          VWAP z-score reversion (P25, $400)
│       ├── vwap_scalper.py            VWAP band scalper (P25, $400)
│       ├── or_reversion.py            Opening range reversion (P28, $400)
│       ├── ema_reversion.py           EMA mean-reversion (P30, $400)
│       ├── sweep_reversal.py          Liquidity sweep reversal (P35, $400)
│       ├── opening_drive.py           Opening drive momentum (P12, $400)
│       ├── kalman_momentum.py         Kalman filter momentum (P40, $400)
│       ├── trend_cont.py              Trend continuation (P40, $400)
│       └── afternoon_momentum.py      PM session momentum (P50, $400)
│
├── backtest/
│   ├── engine_v2.py                   Simulation engine (next-bar-open fills, bias-free)
│   ├── funded_sim.py                  Eval + funded account Monte Carlo simulators
│   └── metrics_v2.py                  Trade metrics and per-model breakdown
│
├── data/
│   ├── loader.py                      CSV loader, resampler, daily bar builder
│   ├── Dataset_NQ_1min_2022_2025.csv  NQ 1-min bars (Dec 2022 – Dec 2025)
│   └── mnq_2026_1min.csv             MNQ 1-min bars (Jan – May 2026)
│
├── live/
│   ├── executor_multi.py              Live executor with bracket orders and risk controls
│   ├── broker_topstep.py              TopStepX REST API client
│   ├── broker_ib.py                   Interactive Brokers TWS API client
│   ├── reporter.py                    Real-time reporting hub
│   ├── alerts.py                      Telegram notification system
│   └── adaptive.py                    Adaptive position sizing guard
│
├── scripts/
│   ├── generate_charts.py             Full backtest + MC + 6 chart PNGs
│   ├── diagnose.py                    Deep diagnostic: exit reasons, timing, streaks
│   ├── show_daily.py                  Daily P&L breakdown
│   ├── show_payout_timeline.py        Funded payout projection
│   └── sweep_permodel.py             Per-model parameter sweep
│
├── output/
│   └── charts/                        Generated chart PNGs
│
├── frontend/
│   ├── index.html                     Interactive dashboard (Chart.js, 4 tabs)
│   └── server.py                      Dashboard API server
│
└── deploy/
    └── ...                            GCP VM deployment scripts
```

---

## Backtest CLI Reference

```bash
python3 run_multi.py --nq <data.csv> [options]
```

| Flag | Description |
|:--|:--|
| `--nq` | Path to 1-minute NQ/MNQ CSV (required) |
| `--history` | Historical data for regime warmup (auto-prepended) |
| `--nq-daily` | Pre-built daily bars CSV (optional) |
| `--csv` | Export trades to CSV file |

---

<p align="center">
  Built for TopStepX 100K funded accounts.<br>
  Bias-free backtesting · Monte Carlo validated · Production-grade live execution
</p>
