"""Account-tier presets for 25K / 50K / 100K / 150K prop-firm accounts.

One codebase, four account types. `apply_tier(cfg, '50k')` mutates a Config in
place to that tier's risk envelope. Each envelope (model_risk_dollars,
max_contracts, daily-loss cap, trailing drawdown, profit target, payout cap) was
tuned and Monte-Carlo-validated (15k sims, 60 trading days, fixed seed) to
MAXIMIZE payout subject to >=90% account survival ("proper risk"). The binding
constraint at the smaller tiers is the tight trailing drawdown, not the contract
cap — so contracts are clipped below the firm cap to hold survival.

⚠️ VERIFY the tier rules (target / drawdown / daily loss / contract cap) against
your prop firm's CURRENT rulebook before trading live — they change, and the 25K
specs in particular should be confirmed.

Risk settings are 1.0x the base per-model risk for every tier (tuning showed the
multiplier is a no-op once the trailing-DD/contract constraint binds).
"""
from __future__ import annotations

# Base per-model risk dollars (1.0x) — identical across tiers.
BASE_RISK = {
    'ou_rev': 500, 'pd_rev': 500, 'vwap_rev': 500, 'or_rev': 500,
    'ou_lunch': 400, 'vwap_scalp': 400, 'open_drive': 600, 'trend': 600,
    'pm_mom': 400, 'ema_rev': 400, 'kalman_mom': 400, 'sweep': 400,
}

# Per-tier funded rules + tuned sizing. daily_profit_target/green_day_* are
# operational intraday throttles scaled to account size (not MC-validated; they
# only further-reduce trading, never increase risk).
TIERS = {
    '25k': dict(max_contracts=9,   dollar_loss_cap=500,  trailing_dd=1500,
                eval_profit_target=1500, max_payout=2000,
                daily_profit_target=200,  green_day_protect=100, green_day_min=75),
    '50k': dict(max_contracts=9,   dollar_loss_cap=1000, trailing_dd=2000,
                eval_profit_target=3000, max_payout=3000,
                daily_profit_target=400,  green_day_protect=150, green_day_min=100),
    '100k': dict(max_contracts=12, dollar_loss_cap=2000, trailing_dd=3000,
                 eval_profit_target=6000, max_payout=5000,
                 daily_profit_target=800,  green_day_protect=250, green_day_min=150),
    '150k': dict(max_contracts=150, dollar_loss_cap=3000, trailing_dd=4500,
                 eval_profit_target=9000, max_payout=7500,
                 daily_profit_target=1200, green_day_protect=375, green_day_min=225),
}

# Funded Monte-Carlo validation of the EXACT presets above (filled in by
# verify_tiers / tier_eval; survival % and avg monthly extraction at proper risk).
TIER_VALIDATION = {
    '25k': dict(survival_pct=91.4, monthly_usd=2030),
    '50k': dict(survival_pct=91.2, monthly_usd=2038),
    '100k': dict(survival_pct=91.6, monthly_usd=2756),
    '150k': dict(survival_pct=90.7, monthly_usd=4168),
}

VALID_TIERS = tuple(TIERS.keys())


def apply_tier(cfg, tier: str):
    """Mutate `cfg` in place to the given account tier. Returns cfg."""
    key = str(tier).lower().replace('$', '').replace('_', '')
    if key not in TIERS:
        raise ValueError(f"unknown tier {tier!r}; valid: {VALID_TIERS}")
    t = TIERS[key]
    cfg.funded.model_risk_dollars = dict(BASE_RISK)
    cfg.risk.max_contracts = t['max_contracts']
    cfg.funded.dollar_loss_cap = t['dollar_loss_cap']
    cfg.funded.trailing_dd = t['trailing_dd']
    cfg.funded.static_threshold = t['trailing_dd']
    cfg.funded.eval_profit_target = t['eval_profit_target']
    cfg.funded.max_payout = t['max_payout']
    cfg.funded.daily_profit_target = t['daily_profit_target']
    cfg.funded.green_day_protect = t['green_day_protect']
    cfg.funded.green_day_min = t['green_day_min']
    return cfg
