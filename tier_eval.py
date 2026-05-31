"""Account-tier evaluation harness.

The strategy edge (R-multiples) is tier-INDEPENDENT — the same 12 models/signals.
What differs per tier is the DOLLAR sizing and the prop-firm funded rules (daily
loss cap, trailing drawdown, max contracts, profit target, payout cap). So we
backtest ONCE, cache the trades, and run a fast funded Monte Carlo per tier
config. Used by the multi-tier tuning workflow and the per-tier validation.
"""
from __future__ import annotations
import os
import pickle
import numpy as np
import pandas as pd
from config import Config
from backtest.funded_sim import run_monte_carlo

CACHE = 'data/_trades_cache.pkl'
MNQ_TICK_VALUE = 0.50  # MNQ live: $0.50/tick ($2/point)


def build_cache(data_path: str = 'data/Dataset_NQ_1min_2022_2025.csv') -> int:
    """Run the backtest once and cache lightweight trade rows + the date index."""
    from data.loader import load_csv, build_daily_bars
    from strategy.multi import MultiModelGenerator
    from backtest.engine_v2 import BacktestEngineV2
    cfg = Config()
    raw = load_csv(data_path, 'NQ')
    daily = build_daily_bars(raw).rename(columns={'date': 'date'})
    daily['date'] = pd.to_datetime(daily['date']).dt.date
    trades = BacktestEngineV2(cfg).run(raw, MultiModelGenerator(cfg).generate(raw, daily, None))
    rows = [{'model': t.model, 'risk_ticks': float(t.risk_ticks),
             'total_r': float(t.total_r), 'date': t.entry_time.date()}
            for t in trades]
    payload = {'rows': rows, 'all_dates': sorted(daily['date'].unique())}
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, 'wb') as f:
        pickle.dump(payload, f)
    return len(rows)


def _load() -> dict:
    if not os.path.exists(CACHE):
        build_cache()
    with open(CACHE, 'rb') as f:
        return pickle.load(f)


def _daily_pnl(rows, all_dates, model_risk: dict, max_contracts: int, dlc: float):
    """Mirror of funded_sim.trades_to_daily_pnl, off cached rows + a tier sizing."""
    default = model_risk.get('_default', 400)
    daily, running = {}, {}
    for t in rows:
        rpc = t['risk_ticks'] * MNQ_TICK_VALUE
        if rpc <= 0:
            continue
        mr = model_risk.get(t['model'], default)
        contracts = min(max_contracts, int(mr / rpc))
        if contracts <= 0:
            continue
        pnl = t['total_r'] * rpc * contracts
        d = t['date']
        if dlc and running.get(d, 0.0) <= -dlc:
            continue
        daily[d] = daily.get(d, 0.0) + pnl
        nr = running.get(d, 0.0) + pnl
        if dlc:
            nr = max(nr, -dlc)
        running[d] = nr
    if dlc:
        for d in daily:
            if daily[d] < -dlc:
                daily[d] = -dlc
    return np.array([daily.get(d, 0.0) for d in all_dates])


def evaluate_tier(*, model_risk: dict, max_contracts: int, dlc: float,
                  trailing_dd: float, target: float, max_payout: float,
                  payout_pct: float = 0.30, n_days: int = 60,
                  n_sims: int = 15000) -> dict:
    """Funded Monte Carlo for one tier config. Returns survival/extraction/monthly."""
    data = _load()
    dp = _daily_pnl(data['rows'], data['all_dates'], model_risk, max_contracts, dlc)
    cfg = Config()
    cfg.funded.dollar_loss_cap = dlc
    cfg.funded.trailing_dd = trailing_dd
    cfg.funded.static_threshold = trailing_dd
    cfg.funded.eval_profit_target = target
    cfg.funded.max_payout = max_payout
    cfg.funded.payout_balance_pct = payout_pct
    mc = run_monte_carlo(dp, cfg, n_sims=n_sims, n_days=n_days)
    active = dp[dp != 0]
    mc['monthly_extraction'] = mc['avg_extraction'] / 3.0  # 60 trading days ~ 3 months
    mc['mean_daily_usd'] = float(active.mean()) if len(active) else 0.0
    return mc


if __name__ == '__main__':
    n = build_cache()
    print(f"cached {n} trades")
    # smoke: current (~100K-ish) settings
    r = evaluate_tier(model_risk={'_default': 400, 'ou_rev': 500, 'pd_rev': 500,
                                  'vwap_rev': 500, 'or_rev': 500, 'open_drive': 600,
                                  'trend': 600},
                      max_contracts=20, dlc=1000, trailing_dd=3000,
                      target=6000, max_payout=5000)
    print(f"smoke 100K-ish: survival={r['survival_rate']:.0f}% "
          f"avg_ext=${r['avg_extraction']:,.0f}/60d  monthly=${r['monthly_extraction']:,.0f}")
