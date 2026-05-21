#!/usr/bin/env python3
"""Automated P($10K) optimization — one variable at a time, walk-forward OOS only."""
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from optimize_p10k import walk_forward_p10k
from config import Config

LOG_FILE = 'docs/optimization_log.md'
THRESHOLD = 0.5
DATA_PATH = 'data/Dataset_NQ_1min_2022_2025.csv'


def read_file(path):
    with open(path) as f:
        return f.read()


def write_file(path, content):
    with open(path, 'w') as f:
        f.write(content)


def evaluate():
    cfg = Config()
    avg_p10k, results = walk_forward_p10k(DATA_PATH, cfg, n_sims=25000)
    return avg_p10k, results


def init_log(baseline):
    with open(LOG_FILE, 'w') as f:
        f.write("# P($10K) Optimization Log\n\n")
        f.write(f"**Baseline P($10K) = {baseline:.2f}%**\n\n")
        f.write("| # | Hypothesis | File | P($10K) | Delta | 2023 | 2024 | 2025 | Result |\n")
        f.write("|---|-----------|------|---------|-------|------|------|------|--------|\n")


def log_result(iteration, hypothesis, filepath, p10k, delta, per_year, kept):
    status = "KEPT" if kept else "REVERTED"
    y23 = per_year.get(2023, {}).get('p10k', 0)
    y24 = per_year.get(2024, {}).get('p10k', 0)
    y25 = per_year.get(2025, {}).get('p10k', 0)
    with open(LOG_FILE, 'a') as f:
        f.write(f"| {iteration} | {hypothesis} | {os.path.basename(filepath)} | "
                f"{p10k:.2f}% | {delta:+.2f}% | {y23:.1f}% | {y24:.1f}% | {y25:.1f}% | "
                f"**{status}** |\n")


def run_one(idx, hypothesis, filepath, old_str, new_str, baseline):
    """Apply one change, evaluate, keep or revert. Returns new baseline."""
    original = read_file(filepath)

    if old_str not in original:
        print(f"  [SKIP] String not found in {filepath}")
        log_result(idx, hypothesis, filepath, baseline, 0, {}, False)
        return baseline

    modified = original.replace(old_str, new_str, 1)
    write_file(filepath, modified)

    try:
        p10k, per_year = evaluate()
    except Exception as e:
        print(f"  [ERROR] {e}")
        write_file(filepath, original)
        log_result(idx, hypothesis, filepath, baseline, 0, {}, False)
        return baseline

    delta = p10k - baseline
    kept = delta > THRESHOLD

    if not kept:
        write_file(filepath, original)

    log_result(idx, hypothesis, filepath, p10k, delta, per_year, kept)

    status = "KEPT" if kept else "REVERTED"
    print(f"  Result: {p10k:.2f}% (delta {delta:+.2f}%) [{status}]")

    return p10k if kept else baseline


def run_two(idx, hypothesis, file1, old1, new1, file2, old2, new2, baseline):
    """Apply two paired changes (e.g., long+short thresholds)."""
    orig1 = read_file(file1)
    orig2 = read_file(file2) if file2 != file1 else orig1

    if old1 not in orig1:
        print(f"  [SKIP] String not found: {old1[:40]}...")
        log_result(idx, hypothesis, file1, baseline, 0, {}, False)
        return baseline

    content1 = orig1.replace(old1, new1, 1)
    write_file(file1, content1)

    if file2 == file1:
        content2 = read_file(file2)
    else:
        content2 = orig2

    if old2 not in content2:
        write_file(file1, orig1)
        print(f"  [SKIP] String not found: {old2[:40]}...")
        log_result(idx, hypothesis, file1, baseline, 0, {}, False)
        return baseline

    content2 = content2.replace(old2, new2, 1)
    write_file(file2, content2)

    try:
        p10k, per_year = evaluate()
    except Exception as e:
        print(f"  [ERROR] {e}")
        write_file(file1, orig1)
        if file2 != file1:
            write_file(file2, orig2)
        log_result(idx, hypothesis, file1, baseline, 0, {}, False)
        return baseline

    delta = p10k - baseline
    kept = delta > THRESHOLD

    if not kept:
        write_file(file1, orig1)
        if file2 != file1:
            write_file(file2, orig2)

    log_result(idx, hypothesis, file1, p10k, delta, per_year, kept)

    status = "KEPT" if kept else "REVERTED"
    print(f"  Result: {p10k:.2f}% (delta {delta:+.2f}%) [{status}]")

    return p10k if kept else baseline


def main():
    baseline = 86.64
    init_log(baseline)
    current = baseline
    no_improve = 0

    OU = 'strategy/models/ou_reversion.py'
    PD = 'strategy/models/pd_level_reversion.py'
    VWAP = 'strategy/models/vwap_reversion.py'
    CFG = 'config.py'
    ENG = 'backtest/engine_v2.py'
    MULTI = 'strategy/multi.py'
    QUAL = 'strategy/quality.py'
    EMA = 'strategy/models/ema_reversion.py'
    KALMAN = 'strategy/models/kalman_momentum.py'
    SWEEP = 'strategy/models/sweep_reversal.py'

    iterations = [
        # (idx, hypothesis, type, ...)
        # type='one' -> (file, old, new)
        # type='two' -> (file1, old1, new1, file2, old2, new2)
    ]

    # Build iteration list
    plan = [
        (1, "OU z-score 2.0→1.8 (more signals)", 'two',
         OU, "ou_z < -2.0", "ou_z < -1.8",
         OU, "ou_z > 2.0", "ou_z > 1.8"),
        (2, "OU z-score 2.0→2.2 (higher conviction)", 'two',
         OU, "ou_z < -2.0", "ou_z < -2.2",
         OU, "ou_z > 2.0", "ou_z > 2.2"),
        (3, "OU risk $2500→$3000", 'one',
         CFG, "'ou_rev': 2500", "'ou_rev': 3000"),
        (4, "PD risk $900→$1800", 'one',
         CFG, "'pd_rev': 900", "'pd_rev': 1800"),
        (5, "Win cap 2.0R→2.5R", 'one',
         ENG, "daily_win_cap: float = 2.0", "daily_win_cap: float = 2.5"),
        (6, "Win cap 2.0R→1.5R", 'one',
         ENG, "daily_win_cap: float = 2.0", "daily_win_cap: float = 1.5"),
        (7, "OU BE trigger 0.6→0.8R", 'one',
         OU, "be_trigger_rr=0.6", "be_trigger_rr=0.8"),
        (8, "OU time stop 35→45min", 'one',
         OU, "time_stop_minutes=35", "time_stop_minutes=45"),
        (9, "OU max daily 5→3", 'one',
         OU, "max_daily=5", "max_daily=3"),
        (10, "Quality min score 4→3", 'one',
         QUAL, "MIN_QUALITY_SCORE = 4", "MIN_QUALITY_SCORE = 3"),
        (11, "Quality min score 4→5", 'one',
         QUAL, "MIN_QUALITY_SCORE = 4", "MIN_QUALITY_SCORE = 5"),
        (12, "OU Hurst cap 0.45→0.48", 'one',
         OU, "hurst > 0.45", "hurst > 0.48"),
        (13, "OU half-life max 25→15", 'one',
         OU, "ou_hl > 25", "ou_hl > 15"),
        (14, "DLC $500→$1500", 'one',
         CFG, "dollar_loss_cap: float = 500.0", "dollar_loss_cap: float = 1500.0"),
        (15, "Trailing DD $3000→$2500", 'one',
         CFG, "trailing_dd: float = 3000.0", "trailing_dd: float = 2500.0"),
        (16, "Static threshold $3000→$2500", 'one',
         CFG, "static_threshold: float = 3000.0", "static_threshold: float = 2500.0"),
        (17, "Post-static scaling 1.25→1.5", 'one',
         CFG, "(3000, 1.25), (0, 1.0)", "(3000, 1.5), (0, 1.0)"),
        (18, "VWAP min_rr 1.3→1.5", 'one',
         VWAP, "min_rr=1.3", "min_rr=1.5"),
        (19, "Tier-3 risk $400→$800", 'one',
         CFG,
         "'vwap_rev': 400, 'ema_rev': 400, 'kalman_mom': 400,\n        'pm_mom': 400, 'sweep': 400, 'trend': 400, 'or_rev': 400,",
         "'vwap_rev': 800, 'ema_rev': 800, 'kalman_mom': 800,\n        'pm_mom': 800, 'sweep': 800, 'trend': 800, 'or_rev': 800,"),
        (20, "Consec loss cooldown 10→6", 'one',
         CFG, "consec_loss_cooldown: int = 10", "consec_loss_cooldown: int = 6"),
        (21, "Signal cutoff 2:30→1:30PM", 'one',
         MULTI, "dt_time(14, 30)", "dt_time(13, 30)"),
        (22, "OU trail 0.1%→0.2%", 'one',
         OU, "trail_pct=0.001", "trail_pct=0.002"),
        (23, "Payout pct 50%→40%", 'one',
         CFG, "payout_balance_pct: float = 0.50", "payout_balance_pct: float = 0.40"),
        (24, "Conflict cooldown 5→3 bars", 'one',
         MULTI, "cooldown_bars: int = 5", "cooldown_bars: int = 3"),
        (25, "Remove OU lunch dead zone", 'one',
         OU,
         "            if dt_time(11, 30) <= t < dt_time(13, 30):\n                continue",
         "            # lunch zone removed for optimization"),
    ]

    for item in plan:
        idx = item[0]
        hypothesis = item[1]
        itype = item[2]

        print(f"\n{'='*60}")
        print(f"  ITERATION {idx}: {hypothesis}")
        print(f"  Current baseline: {current:.2f}%")
        print(f"{'='*60}")

        t0 = time.time()
        prev_baseline = current

        if itype == 'one':
            current = run_one(idx, hypothesis, item[3], item[4], item[5], current)
        elif itype == 'two':
            current = run_two(idx, hypothesis,
                              item[3], item[4], item[5],
                              item[6], item[7], item[8], current)

        elapsed = time.time() - t0
        improved = current > prev_baseline
        print(f"  Elapsed: {elapsed:.0f}s  |  Running baseline: {current:.2f}%")

        if improved:
            no_improve = 0
        else:
            no_improve += 1

        if idx >= 15 and no_improve >= 5:
            print(f"\n  EARLY STOP: 5 consecutive non-improvements after {idx} iterations")
            break

    print(f"\n{'='*60}")
    print(f"  OPTIMIZATION COMPLETE")
    print(f"  Start:  {baseline:.2f}%")
    print(f"  Final:  {current:.2f}%")
    print(f"  Delta:  {current - baseline:+.2f}%")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
