# Multi-Tier Account Configuration (25K / 50K / 100K / 150K)

One codebase, four prop-account types. Each tier has a risk envelope tuned and
**Monte-Carlo-validated to maximize payout subject to ≥90% account survival**
("proper risk"). Select a tier with `--tier`; the fleet can run several tiers in
parallel.

```bash
python3 run_ib.py   --tier 50k          # IB paper/live, 50K rules
python3 run_live.py --tier 100k         # TopStepX, 100K rules
python3 run_multi.py --nq <data> --tier 150k   # backtest a tier
# fleet: set "tier": "150k" per account in fleet_config.json
```

## Validated per-tier results

Funded Monte Carlo — 15,000 simulations, 60 trading days (~3 months), fixed seed
(deterministic), off the 3-year (2,483-trade) backtest. Risk = 1.0× base for
every tier (tuning showed the multiplier is a no-op once the binding constraint
hits).

| Tier | Profit target | Trailing DD | Daily-loss cap | Max MNQ | Survival | Avg extraction / mo |
|------|--------------|-------------|----------------|---------|----------|---------------------|
| **25K** | $1,500 | $1,500 | $500 | 9¹ | **91.4%** | **~$2,030** |
| **50K** | $3,000 | $2,000 | $1,000 | 9¹ | **91.2%** | **~$2,038** |
| **100K** | $6,000 | $3,000 | $2,000 | 12¹ | **91.6%** | **~$2,756** |
| **150K** | $9,000 | $4,500 | $3,000 | ~20² | **90.7%** | **~$4,168** |

¹ Contracts are **clipped below the firm cap** (25K=20, 50K=50, 100K=100): the
tight trailing drawdown — not the contract cap — is the binding constraint at the
smaller tiers. Running the full cap drops survival below 90%.
² At 150K the trailing-DD cushion is large enough that sizing is governed by
per-model risk (~20 MNQ), not the 150 cap — so the cap never binds.

## Why the numbers are what they are (the honest part)

- **The edge is tier-independent.** Same 12 models, same signals, same
  out-of-sample walk-forward edge (+0.22R/trade, 79% avg P($10K/60d) across 3
  held-out years). Tiers differ only in **dollar sizing** and **funded rules**.
- **Variance is the enemy of extraction under a trailing drawdown.** Bigger size
  = bigger swings = more blow-ups = *less* extracted. That's why every tier lands
  at 1.0× risk and why the smaller tiers must clip contracts.
- **Monotonic but sub-linear:** $2,030 → $2,038 → $2,756 → $4,168. The 25K and
  50K are nearly equal because both are squeezed to ~9 contracts by their tight
  drawdowns.

## Reaching $10K/month (proper risk)

No single account hits $10K/month at proper risk — the trailing-DD ceiling caps
it. The route is **parallel accounts** (the fleet runner), e.g.:

- 3 × 150K ≈ **~$12.5K/mo**, or
- 2 × 150K + 1 × 100K ≈ **~$11.1K/mo**, or
- a mix sized to your capital and eval-fee budget.

⚠️ Parallel accounts running the same strategy are **correlated leverage** — 3×
the P&L *and* 3× the drawdown in a bad month. Survival is per-account (~91%); a
bad market month hits all of them together.

## Before live — non-negotiable

1. **Verify the tier rules** against your firm's *current* rulebook (`tiers.py`
   `TIERS`). The 25K specs especially should be confirmed.
2. **Paper-validate** the exact tier: `run_ib.py --shadow --tier <t>` →
   `validate_forward.py`. Scale to real capital only on a `GO`.
3. These are **backtest/MC projections.** Live takes a haircut (fill model, the
   $X daily-profit ceiling) and the market is stochastic — no number here is a
   guarantee.

## Methodology (for technical review)

- **Validation:** out-of-sample walk-forward (train-on-N-years / test-on-holdout)
  + block-bootstrap Monte Carlo funded-account simulation (trailing DD, static
  phase, payout cadence). Reproducible: fixed seed, `tier_eval.py` + `tiers.py`.
- **Optimization:** per-tier sizing chosen to maximize expected extraction subject
  to a hard survival floor — a constrained objective, not an unconstrained
  return-max (which overfits and blows accounts).
- **Significance:** per-(model,direction) edges tested with t-stats + train/test
  stability; no statistically significant negative combo exists, so none are cut.
