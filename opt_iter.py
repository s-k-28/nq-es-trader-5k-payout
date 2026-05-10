#!/usr/bin/env python3
"""Run a single optimization iteration — applies change, evaluates via subprocess, reverts.

Usage: python3 opt_iter.py <iteration_number>
Output: JSON with p10k result on last line.
Always reverts the file after evaluation so the codebase stays clean.
"""
import sys
import os
import subprocess
import json

def read_file(path):
    with open(path) as f:
        return f.read()

def write_file(path, content):
    with open(path, 'w') as f:
        f.write(content)

def evaluate():
    """Run optimize_p10k.py as a subprocess so all imports are fresh."""
    result = subprocess.run(
        [sys.executable, '-c', '''
import sys, os
sys.path.insert(0, ".")
from optimize_p10k import walk_forward_p10k
from config import Config
import json

cfg = Config()
avg_p10k, results = walk_forward_p10k("data/Dataset_NQ_1min_2022_2025.csv", cfg, n_sims=25000)
out = {
    "p10k": round(avg_p10k, 2),
    "per_year": {str(k): round(v.get("p10k", 0), 2) for k, v in results.items()},
}
print("RESULT:" + json.dumps(out))
'''],
        capture_output=True, text=True, timeout=600
    )
    for line in result.stdout.strip().split('\n'):
        if line.startswith('RESULT:'):
            return json.loads(line[7:])
    print(f"STDERR: {result.stderr[-500:]}" if result.stderr else "no stderr")
    print(f"STDOUT: {result.stdout[-500:]}" if result.stdout else "no stdout")
    raise RuntimeError("No RESULT line in subprocess output")

def run_single(hypothesis, filepath, old_str, new_str):
    original = read_file(filepath)
    if old_str not in original:
        return {"p10k": 0, "skip": True, "reason": f"string not found in {filepath}"}

    modified = original.replace(old_str, new_str, 1)
    write_file(filepath, modified)
    try:
        result = evaluate()
        result["hypothesis"] = hypothesis
        return result
    except Exception as e:
        return {"p10k": 0, "error": str(e)}
    finally:
        write_file(filepath, original)

def run_pair(hypothesis, f1, o1, n1, f2, o2, n2):
    orig1 = read_file(f1)
    if o1 not in orig1:
        return {"p10k": 0, "skip": True, "reason": f"'{o1[:30]}' not in {f1}"}

    write_file(f1, orig1.replace(o1, n1, 1))

    if f2 == f1:
        cur2 = read_file(f2)
    else:
        cur2 = read_file(f2)
    orig2_content = read_file(f2) if f2 != f1 else orig1

    if o2 not in cur2:
        write_file(f1, orig1)
        return {"p10k": 0, "skip": True, "reason": f"'{o2[:30]}' not in {f2}"}

    write_file(f2, cur2.replace(o2, n2, 1))

    try:
        result = evaluate()
        result["hypothesis"] = hypothesis
        return result
    except Exception as e:
        return {"p10k": 0, "error": str(e)}
    finally:
        write_file(f1, orig1)
        if f2 != f1:
            write_file(f2, orig2_content)

if __name__ == '__main__':
    idx = int(sys.argv[1])

    OU = 'strategy/models/ou_reversion.py'
    CFG = 'config.py'
    ENG = 'backtest/engine_v2.py'
    MULTI = 'strategy/multi.py'
    QUAL = 'strategy/quality.py'
    VWAP_M = 'strategy/models/vwap_reversion.py'

    iters = {
        1: lambda: run_pair("OU z-score 2.0->1.8", OU, "ou_z < -2.0", "ou_z < -1.8", OU, "ou_z > 2.0", "ou_z > 1.8"),
        2: lambda: run_pair("OU z-score 2.0->2.2", OU, "ou_z < -2.0", "ou_z < -2.2", OU, "ou_z > 2.0", "ou_z > 2.2"),
        3: lambda: run_single("OU risk $2500->$3000", CFG, "'ou_rev': 2500", "'ou_rev': 3000"),
        4: lambda: run_single("PD risk $900->$1800", CFG, "'pd_rev': 900", "'pd_rev': 1800"),
        5: lambda: run_single("Win cap 2.0R->2.5R", ENG, "daily_win_cap: float = 2.0", "daily_win_cap: float = 2.5"),
        6: lambda: run_single("Win cap 2.0R->1.5R", ENG, "daily_win_cap: float = 2.0", "daily_win_cap: float = 1.5"),
        7: lambda: run_single("OU BE trigger 0.6->0.8R", OU, "be_trigger_rr=0.6", "be_trigger_rr=0.8"),
        8: lambda: run_single("OU time stop 35->45min", OU, "time_stop_minutes=35", "time_stop_minutes=45"),
        9: lambda: run_single("OU max daily 5->3", OU, "max_daily=5", "max_daily=3"),
        10: lambda: run_single("Quality min score 4->3", QUAL, "MIN_QUALITY_SCORE = 4", "MIN_QUALITY_SCORE = 3"),
        11: lambda: run_single("Quality min score 4->5", QUAL, "MIN_QUALITY_SCORE = 4", "MIN_QUALITY_SCORE = 5"),
        12: lambda: run_single("OU Hurst cap 0.45->0.48", OU, "hurst > 0.45", "hurst > 0.48"),
        13: lambda: run_single("OU half-life max 25->15", OU, "ou_hl > 25", "ou_hl > 15"),
        14: lambda: run_single("DLC $500->$1500", CFG, "dollar_loss_cap: float = 500.0", "dollar_loss_cap: float = 1500.0"),
        15: lambda: run_single("Trailing DD $3000->$2500", CFG, "trailing_dd: float = 3000.0", "trailing_dd: float = 2500.0"),
        16: lambda: run_single("Static threshold $3000->$2500", CFG, "static_threshold: float = 3000.0", "static_threshold: float = 2500.0"),
        17: lambda: run_single("Post-static scaling 1.25->1.5x", CFG, "(3000, 1.25), (0, 1.0)", "(3000, 1.5), (0, 1.0)"),
        18: lambda: run_single("VWAP min_rr 1.3->1.5", VWAP_M, "min_rr=1.3", "min_rr=1.5"),
        19: lambda: run_single("Tier-3 risk $400->$800", CFG,
            "'vwap_rev': 400, 'ema_rev': 400, 'kalman_mom': 400,\n        'pm_mom': 400, 'sweep': 400, 'trend': 400, 'or_rev': 400,",
            "'vwap_rev': 800, 'ema_rev': 800, 'kalman_mom': 800,\n        'pm_mom': 800, 'sweep': 800, 'trend': 800, 'or_rev': 800,"),
        20: lambda: run_single("Consec loss cooldown 10->6", CFG, "consec_loss_cooldown: int = 10", "consec_loss_cooldown: int = 6"),
        21: lambda: run_single("Signal cutoff 2:30->1:30PM", MULTI, "dt_time(14, 30)", "dt_time(13, 30)"),
        22: lambda: run_single("OU trail 0.1%->0.2%", OU, "trail_pct=0.001", "trail_pct=0.002"),
        23: lambda: run_single("Payout pct 30%->40%", CFG, "payout_balance_pct: float = 0.30", "payout_balance_pct: float = 0.40"),
        24: lambda: run_single("Conflict cooldown 5->3 bars", MULTI, "cooldown_bars: int = 5", "cooldown_bars: int = 3"),
        25: lambda: run_single("Remove OU lunch dead zone", OU,
            "            if dt_time(11, 30) <= t < dt_time(13, 30):\n                continue",
            "            # lunch zone removed"),
    }

    if idx not in iters:
        print(json.dumps({"error": f"Unknown iteration {idx}"}))
        sys.exit(1)

    result = iters[idx]()
    print(json.dumps(result))
