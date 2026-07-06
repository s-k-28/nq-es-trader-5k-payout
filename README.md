<div align="center">

# NQ Quant Trader: 5K Payout System

### A 12-model MNQ futures day-trading system engineered for funded accounts

**One codebase that backtests without look-ahead bias, validates with 25,000 Monte Carlo simulations, and trades live on TopStepX and Interactive Brokers with the exact same signal logic.**

[![Python](https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square)](https://www.python.org/)
[![pandas](https://img.shields.io/badge/pandas-2.0%2B-150458?style=flat-square)](https://pandas.pydata.org/)
[![ib_insync](https://img.shields.io/badge/ib__insync-0.9.86%2B-red?style=flat-square)](https://github.com/erdewit/ib_insync)
[![Models](https://img.shields.io/badge/models-12-green?style=flat-square)](#the-12-models)
[![Backtest](https://img.shields.io/badge/backtest-2%2C716%20trades%2C%20PF%201.66-orange?style=flat-square)](#validation-and-results)
[![Monte Carlo](https://img.shields.io/badge/eval%20pass%20rate-93.7%25%20(25K%20sims)-purple?style=flat-square)](#validation-and-results)
[![License](https://img.shields.io/badge/license-MIT-lightgrey?style=flat-square)](LICENSE)

</div>

---

## What it does

This system day-trades Micro E-mini Nasdaq-100 futures (MNQ) on prop-firm funded accounts. Twelve independent quantitative models scan 1-minute bars, a priority resolver picks at most one trade at a time, and a risk layer enforces the account rules that actually kill funded traders: the trailing drawdown, the daily loss cap, and overnight exposure (there is none; everything flattens at 3:55 PM ET).

The "5K payout" in the name is the goal the whole machine is optimized for: pass a $100K evaluation ($6,000 target, $3,000 trailing drawdown), then extract payouts of up to $5,000 while staying above the drawdown floor. Every design decision, from the 2.0R daily win cap to the 10-loss cooldown, exists to maximize the probability of repeated payouts rather than raw returns.

Three run modes share one signal engine:

- **Backtest**: `run_multi.py` replays 1,048,566 one-minute NQ bars (Dec 2022 to Dec 2025) plus a held-out 2026 MNQ file through a bias-free simulator.
- **Paper**: `run_paper.py` trades on Yahoo Finance data with simulated fills, no account needed.
- **Live**: `run_live.py` (TopStepX / ProjectX REST API) and `run_ib.py` (Interactive Brokers via ib_insync) place real bracket orders, and `run_fleet.py` supervises multiple accounts at once.

## Why it's different

| | Typical retail trading bot | This system |
|---|---|---|
| Fill model | Same-bar close (look-ahead bias) | Next-bar open, gap filter discards untradeable entries |
| Costs | Ignored | 0.25-tick adverse slippage on every non-target exit |
| Signal logic | One indicator strategy | 12 independent models, priority-resolved, one position at a time |
| Validation | One in-sample equity curve | Per-year walk-forward splits plus 25,000-run Monte Carlo of the full eval-to-payout lifecycle |
| Account rules | An afterthought | Trailing DD, daily loss cap, green-day payout logic simulated and enforced in live code |
| Live safety | Hope | OCA brackets, entry timeouts, orphan detection, crash-safe state restore, 1,516-line robustness test file |
| Position sizing | Fixed | Model-tiered risk dollars plus an adaptive guard that scales models by recent live performance |

## Architecture

```
                       1-minute bars (backtest CSV / TopStepX / IB / Yahoo)
                                          |
                                          v
        +--------------------------------------------------------------+
        |  strategy/quant/features.py                                  |
        |  OU half-life, Hurst exponent, Kalman filter, Parkinson vol, |
        |  VWAP bands, opening range   (120-day regime warmup)         |
        +-------------------------------+------------------------------+
                                        |
                                        v
        +--------------------------------------------------------------+
        |  12 models (strategy/models/)                                 |
        |  mean reversion: ou_rev  pd_rev  vwap_rev  or_rev  ou_lunch   |
        |                  vwap_scalp  ema_rev  sweep                   |
        |  momentum:       open_drive  trend  pm_mom  kalman_mom        |
        +-------------------------------+------------------------------+
                                        |
                                        v
        +--------------------------------------------------------------+
        |  strategy/multi.py : priority resolver                       |
        |  3:30 PM cutoff -> quality filter (OU needs Q >= 4) ->        |
        |  lowest priority number wins, ties broken by risk-reward ->   |
        |  3-bar cooldown, single open position                         |
        +-------------------------------+------------------------------+
                                        |
                    +-------------------+-------------------+
                    v                                       v
     +-----------------------------+       +-----------------------------------+
     |  backtest/engine_v2.py      |       |  live/executor_multi.py           |
     |  next-bar-open fills        |       |  limit entry, 60s timeout         |
     |  gap filter, slippage       |       |  OCA bracket (stop + target)      |
     |  2.0R day cap, cooldowns    |       |  breakeven at 0.6R, trailing stop |
     |  funded_sim.py Monte Carlo  |       |  3:55 PM flatten, orphan checks   |
     +--------------+--------------+       |  Telegram alerts (18 events)      |
                    |                      |  decisions.jsonl state journal    |
                    v                      +-----------------+-----------------+
     +-----------------------------+                         |
     |  frontend/ dashboard        |                         v
     |  charts, trade forensics,   |       +-----------------------------------+
     |  Monte Carlo, rules (:8080) |       |  brokers: TopStepX REST,          |
     +-----------------------------+       |  IB (ib_insync), paper (Yahoo)    |
                                           +-----------------------------------+
```

## The 12 models

Eight mean-reversion and four momentum models, each with its own risk profile and priority. Bias-free per-model results over the full Dec 2022 to May 2026 backtest:

```
  Total R contribution by model (2,716 trades)

  ou_rev       +175.2R  |###################################
  or_rev       + 91.4R  |##################
  trend        + 68.5R  |##############
  kalman_mom   + 68.2R  |##############
  pm_mom       + 66.5R  |#############
  vwap_scalp   + 49.1R  |##########
  ou_lunch     + 39.6R  |########
  open_drive   + 23.3R  |#####
  ema_rev      + 22.8R  |#####
  pd_rev       + 21.0R  |####
  vwap_rev     + 19.2R  |####
  sweep        +  2.5R  |#
```

All twelve are net positive across the full sample. Ornstein-Uhlenbeck reversion (`ou_rev`) is the workhorse (598 trades, 48% win rate, +0.293R expectancy) and carries the highest risk allocation; lower-edge models get smaller risk dollars.

## Validation and results

All numbers below are simulated backtest and Monte Carlo results produced by this repository's engine on historical data. They are not live trading records.

**Full backtest, Dec 2022 to May 2026:** 2,716 trades over 792 trading days (3.4 per day), 44.6% win rate, average win +1.34R against average loss -0.65R, expectancy +0.238R per trade, profit factor 1.66, total +647.5R, max drawdown -12.6R.

**Yearly walk-forward splits** (each year traded out-of-sample, with prior years used only as regime warmup):

```
  Cumulative R over time

  end 2023      +164.6R  |################                 851 trades, 44% WR
  end 2024      +342.7R  |##################################        971 trades, 42% WR
  end 2025      +546.4R  |######################################################   756 trades, 44% WR
  Apr 2026      +646.2R  |################################################################  229 trades, 48% WR
```

**Monte Carlo of the funded lifecycle** (25,000 simulations of a $100K TopStep-style account):

| Metric | Result |
|---|---|
| Evaluation pass rate ($6K target, $3K trailing DD) | 93.7% |
| Median days to pass evaluation | 15 |
| 60-day funded survival | 97.2% |
| Probability of extracting $5K+ | 92.4% |
| Probability of extracting $10K+ | 81.5% |
| Average 60-day extraction | $14,276 |

`validate_forward.py` closes the loop: it compares live decision logs against the backtest baseline (expectancy floor, win-rate tolerance, daily-cap discipline) and issues a GO / NO-GO verdict before capital scales up.

## Funded account rules enforced

| Rule | Value | Where |
|---|---|---|
| Evaluation profit target | $6,000 | `config.py`, `backtest/funded_sim.py` |
| Trailing drawdown | $3,000 (floor locks at $0 once peak profit hits $3K) | `config.py` |
| Daily loss cap | $1,000 | engine and live executor |
| Daily win cap | 2.0R, then stop trading for the day | `backtest/engine_v2.py` |
| Payout | Max $5,000, capped at 30% of balance, after 5 green days ($150+) | `config.py` |
| Max position | 20 MNQ contracts | `config.py` |
| Overnight risk | None: hard flatten at 3:55 PM ET | engine and executor |

`tiers.py` ships presets for 25K, 50K, 100K, and 150K account sizes; the 100K default is Monte Carlo validated at 91.6% survival with roughly $2,756 median monthly extraction.

## Quickstart

```bash
git clone https://github.com/s-k-28/nq-es-trader-5k-payout.git
cd nq-es-trader-5k-payout
pip install -r requirements.txt

# 1. Backtest the full 2022-2025 dataset
python3 run_multi.py --nq data/Dataset_NQ_1min_2022_2025.csv

# 2. Out-of-sample 2026 with historical regime warmup
python3 run_multi.py --nq data/mnq_2026_1min.csv --history data/Dataset_NQ_1min_2022_2025.csv

# 3. Charts + Monte Carlo (writes 6 PNGs to output/charts/)
python3 scripts/generate_charts.py

# 4. Dashboard at http://localhost:8080
python3 frontend/server.py

# 5. Paper trade with no account
python3 run_paper.py

# 6. Live on TopStepX (fill in .env first)
cp .env.example .env    # TOPSTEP_USER, TOPSTEP_API_KEY, TOPSTEP_ENV
python3 run_live.py --shadow     # signals only, no orders
python3 run_live.py --env demo   # practice account
python3 run_live.py --env live   # real account

# 7. Live on Interactive Brokers (IB Gateway running)
python3 run_ib.py                # paper, port 4002
python3 run_ib.py --port 4001    # live

# 8. Run a whole fleet of accounts
python3 run_fleet.py --config fleet_config.json
```

## Tech stack

| Component | Tool | Role |
|---|---|---|
| Language | Python 3.10+ | Everything |
| Data | pandas, numpy | 1-minute bar processing, feature computation |
| Quant features | Custom (no ML frameworks) | OU half-life, Hurst, Kalman filter, Parkinson volatility |
| Broker: prop | TopStepX / ProjectX REST API | Funded account execution, 30-second tick loop |
| Broker: retail | ib_insync | IB Gateway bracket orders, OCA groups |
| Alerts | Telegram Bot API (requests) | 18 event types: fills, exits, risk breaches, restarts |
| Dashboard | Python http.server + static HTML/JS | Charts, trade forensics, Monte Carlo, strategy rules |
| Charts | matplotlib | Equity, drawdown, per-model, Monte Carlo PNGs |
| Config | python-dotenv | Credentials and environment switching |

## Project structure

```
nq-es-trader-5k-payout/
    config.py                # Instrument, sessions, risk, funded-account rules
    run_multi.py             # Backtest batch runner
    run_live.py              # TopStepX live entry point
    run_ib.py                # Interactive Brokers entry point
    run_paper.py             # Yahoo Finance paper trading
    run_fleet.py             # Multi-account fleet supervisor
    tiers.py / tier_eval.py  # 25K/50K/100K/150K presets + validation
    validate_forward.py      # Live-vs-backtest GO / NO-GO gate
    strategy/
        multi.py             # Signal orchestrator and priority resolver
        models/              # The 12 models + base classes and registry
        quant/features.py    # OU, Hurst, Kalman, Parkinson, VWAP bands
    backtest/
        engine_v2.py         # Bias-free simulator: next-bar fills, gap filter
        funded_sim.py        # Evaluation + funded Monte Carlo (25K runs)
        metrics_v2.py        # Trade metrics, per-model breakdowns
    live/
        executor_multi.py    # Brackets, breakeven, trailing, flatten, restarts
        broker_topstep.py    # TopStepX REST client with token refresh
        broker_ib.py         # ib_insync wrapper
        alerts.py            # Telegram notifications
        adaptive.py          # Performance-based model scaling guard
        state/               # decisions.jsonl, crash-safe journals
    data/                    # 1-min NQ 2022-2025 + 2026 MNQ out-of-sample
    frontend/                # Dashboard server + single-page UI
    scripts/                 # Chart generation, diagnostics, payout timeline
    tests/                   # 2,449 lines: live robustness, fleet, brackets
    deploy/                  # GCP VM deployment scripts
```

## Testing

2,449 lines of tests across 7 files. The largest, `tests/test_live_robustness.py` (1,516 lines), attacks the failure modes that matter with real money: token refresh races, order placement timeouts, daily-cap enforcement, orphan position cleanup, mid-session restart reconstruction, and shutdown safety during a flatten.

## Disclaimer

This project is for education and research. It is not investment advice, and nothing here is a solicitation to trade futures. All performance figures are simulated backtest and Monte Carlo results generated by this repository's code on historical data; they are not live trading records, and simulated results routinely overstate what is achievable in live markets. Futures trading carries substantial risk of loss, funded-account programs have their own terms and failure modes, and past performance, simulated or real, does not guarantee future results. Trade at your own risk.

## License

MIT License. See [LICENSE](LICENSE).
