#!/usr/bin/env python3
"""Per-model parameter sweep — test if individual models benefit from
different partial_pct, trail_pct, or time_stop settings."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import argparse
import copy
import pandas as pd
from config import Config
from data.loader import load_csv, build_daily_bars
from strategy.models.base import ModelRiskProfile
from strategy.models.ou_reversion import OUReversionModel
from strategy.models.vwap_reversion import VWAPReversionModel
from strategy.models.trend_cont import TrendContinuationModel
from strategy.models.sweep_reversal import SweepReversalModel
from strategy.multi import MultiModelGenerator
from backtest.engine_v2 import BacktestEngineV2
from backtest.metrics_v2 import MetricsV2


def run_with_overrides(raw, daily, cfg, overrides: dict):
    """Run full pipeline with per-model risk_profile overrides.
    overrides: {model_name: {param: value, ...}}
    """
    gen = MultiModelGenerator(cfg)
    for model in gen.models:
        if model.name in overrides:
            rp = model.risk_profile
            for k, v in overrides[model.name].items():
                setattr(rp, k, v)

    signals = gen.generate(raw, daily, None)
    engine = BacktestEngineV2(cfg)
    trades = engine.run(raw, signals)
    m = MetricsV2(trades, cfg)

    r = m.funded_sim(contracts=20, target_usd=3000, max_dd_usd=2000,
                     window_days=20, trailing_dd=True,
                     daily_loss_limit_usd=1000)

    model_stats = {}
    if not m.df.empty:
        for model, grp in m.df.groupby('model'):
            model_stats[model] = {
                'n': len(grp),
                'wr': (grp['total_r'] > 0).mean(),
                'total_r': grp['total_r'].sum(),
                'avg_r': grp['total_r'].mean(),
            }

    return {
        'trades': len(trades),
        'total_r': m.df['total_r'].sum() if not m.df.empty else 0,
        'wr': (m.df['total_r'] > 0).mean() if not m.df.empty else 0,
        'eval': r['pass_rate'],
        'model_stats': model_stats,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--nq', required=True)
    args = p.parse_args()

    cfg = Config()
    raw = load_csv(args.nq, 'NQ')
    daily = build_daily_bars(raw)
    daily = daily.rename(columns={'date': 'date'})
    daily['date'] = pd.to_datetime(daily['date']).dt.date

    # Baseline
    base = run_with_overrides(raw, daily, cfg, {})
    print(f"Baseline: {base['trades']} trades, +{base['total_r']:.1f}R, "
          f"WR={base['wr']:.1%}")
    print(f"  Eval 9MNQ: {base['eval']:.1f}%")
    for mn, ms in base['model_stats'].items():
        print(f"  {mn}: {ms['n']} trades, {ms['wr']:.0%} WR, +{ms['total_r']:.1f}R")

    # === OU partial_pct sweep ===
    print(f"\n=== OU partial_pct sweep ===")
    for pp in [0.3, 0.4, 0.5, 0.6, 0.7]:
        r = run_with_overrides(raw, daily, cfg, {'ou_rev': {'partial_pct': pp}})
        ou = r['model_stats'].get('ou_rev', {})
        print(f"  ou partial_pct={pp:.1f}: {r['trades']} trades +{r['total_r']:.1f}R "
              f"eval={r['eval']:.1f}% | ou: {ou.get('n',0)} trades {ou.get('wr',0):.0%} WR +{ou.get('total_r',0):.1f}R")

    # === VWAP partial_pct sweep ===
    print(f"\n=== VWAP partial_pct sweep ===")
    for pp in [0.3, 0.4, 0.5, 0.6, 0.7]:
        r = run_with_overrides(raw, daily, cfg, {'vwap_rev': {'partial_pct': pp}})
        vw = r['model_stats'].get('vwap_rev', {})
        print(f"  vwap partial_pct={pp:.1f}: {r['trades']} trades +{r['total_r']:.1f}R "
              f"eval={r['eval']:.1f}% | vwap: {vw.get('n',0)} trades {vw.get('wr',0):.0%} WR +{vw.get('total_r',0):.1f}R")

    # === Trend partial_pct sweep ===
    print(f"\n=== Trend partial_pct sweep (currently 0.3) ===")
    for pp in [0.2, 0.3, 0.4, 0.5, 0.6]:
        r = run_with_overrides(raw, daily, cfg, {'trend': {'partial_pct': pp}})
        tr = r['model_stats'].get('trend', {})
        print(f"  trend partial_pct={pp:.1f}: {r['trades']} trades +{r['total_r']:.1f}R "
              f"eval={r['eval']:.1f}% | trend: {tr.get('n',0)} trades {tr.get('wr',0):.0%} WR +{tr.get('total_r',0):.1f}R")

    # === Trail_pct for OU ===
    print(f"\n=== OU trail_pct sweep ===")
    for tp in [0.0, 0.2, 0.3, 0.4, 0.5]:
        r = run_with_overrides(raw, daily, cfg, {'ou_rev': {'trail_pct': tp}})
        ou = r['model_stats'].get('ou_rev', {})
        print(f"  ou trail={tp:.1f}: {r['trades']} trades +{r['total_r']:.1f}R "
              f"eval={r['eval']:.1f}% | ou: {ou.get('n',0)} trades {ou.get('wr',0):.0%} WR +{ou.get('total_r',0):.1f}R")

    # === Trail_pct for VWAP ===
    print(f"\n=== VWAP trail_pct sweep ===")
    for tp in [0.0, 0.2, 0.3, 0.4, 0.5]:
        r = run_with_overrides(raw, daily, cfg, {'vwap_rev': {'trail_pct': tp}})
        vw = r['model_stats'].get('vwap_rev', {})
        print(f"  vwap trail={tp:.1f}: {r['trades']} trades +{r['total_r']:.1f}R "
              f"eval={r['eval']:.1f}% | vwap: {vw.get('n',0)} trades {vw.get('wr',0):.0%} WR +{vw.get('total_r',0):.1f}R")

    # === Trend trail_pct sweep (currently 0.4) ===
    print(f"\n=== Trend trail_pct sweep (currently 0.4) ===")
    for tp in [0.0, 0.2, 0.3, 0.4, 0.5, 0.6]:
        r = run_with_overrides(raw, daily, cfg, {'trend': {'trail_pct': tp}})
        tr = r['model_stats'].get('trend', {})
        print(f"  trend trail={tp:.1f}: {r['trades']} trades +{r['total_r']:.1f}R "
              f"eval={r['eval']:.1f}% | trend: {tr.get('n',0)} trades {tr.get('wr',0):.0%} WR +{tr.get('total_r',0):.1f}R")

    # === Time stop per model ===
    print(f"\n=== OU time_stop sweep (currently 35) ===")
    for ts in [20, 25, 30, 35, 40, 50]:
        r = run_with_overrides(raw, daily, cfg, {'ou_rev': {'time_stop_minutes': ts}})
        ou = r['model_stats'].get('ou_rev', {})
        print(f"  ou time_stop={ts}: {r['trades']} trades +{r['total_r']:.1f}R "
              f"eval={r['eval']:.1f}% | ou: {ou.get('n',0)} trades {ou.get('wr',0):.0%} WR +{ou.get('total_r',0):.1f}R")

    print(f"\n=== VWAP time_stop sweep (currently 30) ===")
    for ts in [15, 20, 25, 30, 35, 40]:
        r = run_with_overrides(raw, daily, cfg, {'vwap_rev': {'time_stop_minutes': ts}})
        vw = r['model_stats'].get('vwap_rev', {})
        print(f"  vwap time_stop={ts}: {r['trades']} trades +{r['total_r']:.1f}R "
              f"eval={r['eval']:.1f}% | vwap: {vw.get('n',0)} trades {vw.get('wr',0):.0%} WR +{vw.get('total_r',0):.1f}R")

    # === Sweep max_daily cap ===
    print(f"\n=== Sweep max_daily sweep (currently 2) ===")
    for md in [1, 2, 3]:
        r = run_with_overrides(raw, daily, cfg, {'sweep': {'max_daily': md}})
        sw = r['model_stats'].get('sweep', {})
        print(f"  sweep max_daily={md}: {r['trades']} trades +{r['total_r']:.1f}R "
              f"eval={r['eval']:.1f}% | sweep: {sw.get('n',0)} trades {sw.get('wr',0):.0%} WR +{sw.get('total_r',0):.1f}R")

    # === Combo: best individual changes ===
    print(f"\n=== Combined best candidates (will test after seeing individual results) ===")
    print("  (check individual results above and combine winners)")


if __name__ == '__main__':
    main()
