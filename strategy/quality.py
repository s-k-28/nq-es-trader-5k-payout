"""Signal quality scoring — filters signals by composite quality score.

Scores each signal 0-12 based on: direction, day-of-week, time-of-day,
OU half-life, OU z-score magnitude, Hurst exponent, and R:R quality.
"""
from __future__ import annotations
import pandas as pd
from strategy.models.base import Signal

MIN_QUALITY_SCORE = 3


def score_signal(sig: Signal, df: pd.DataFrame) -> int:
    score = 0
    bar = df.iloc[sig.idx] if sig.idx < len(df) else None

    if sig.direction == 'long':
        score += 1

    wd = sig.ts.weekday()
    if wd == 3:
        score += 1
    elif wd == 4:
        score += 2
    elif wd == 2:
        score -= 1

    hr = sig.ts.hour
    if hr == 10:
        score += 1
    elif hr == 14:
        score += 1

    if bar is not None:
        if sig.model == 'ou_rev':
            hl = bar.get('ou_half_life')
            if not pd.isna(hl):
                if hl <= 5:
                    score += 3
                elif hl <= 10:
                    score += 1
                elif hl > 10:
                    score -= 3

            z = bar.get('ou_zscore')
            if not pd.isna(z):
                az = abs(z)
                if 2.0 <= az <= 2.5:
                    score += 2
                elif 2.5 < az <= 3.0:
                    score += 1
                elif az > 3.0:
                    score -= 1

        h = bar.get('hurst')
        if not pd.isna(h) and sig.model in ('ou_rev', 'vwap_rev'):
            if h < 0.35:
                score += 3
            elif h < 0.40:
                score += 2
            elif h < 0.45:
                score += 1
            elif h >= 0.50:
                score -= 1

    if 2.0 <= sig.rr <= 2.5:
        score += 1

    return score


def filter_by_quality(signals: list[Signal], df: pd.DataFrame,
                      min_score: int = MIN_QUALITY_SCORE) -> list[Signal]:
    return [s for s in signals
            if s.model != 'ou_rev' or score_signal(s, df) >= min_score]
