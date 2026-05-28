"""Adaptive learning layer -- defensive guards that learn from live performance.

Does NOT change the core strategy. Only scales DOWN or pauses when conditions
indicate higher risk. All decisions are logged and journaled for human review.

PostTradeAnalyzer classifies WHY trades fail and builds conditional filters
so the bot doesn't repeat the same mistakes.
"""
from __future__ import annotations

import json
import logging
import threading
from collections import deque, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TradeRecord:
    """Single trade observation for rolling tracking."""
    timestamp: str
    model: str
    total_r: float
    pnl_usd: float
    hour: int
    atr_ratio: float  # current_atr / avg_atr
    win: bool = False

    def __post_init__(self):
        self.win = self.total_r > 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TradeRecord":
        d = dict(d)  # avoid mutating caller's dict
        d.pop('win', None)  # recalculated in __post_init__
        return cls(**d)


@dataclass
class JournalEntry:
    """Full record of an entry decision for audit trail."""
    timestamp: str
    model: str
    direction: str
    total_r: float
    pnl_usd: float
    confidence: float
    hour: int
    atr_ratio: float
    guards_fired: list = field(default_factory=list)
    volatility_scale: float = 1.0
    final_scale: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ROLLING_WINDOW = 20         # last N trades per model
MIN_WIN_RATE = 0.25         # below this -> half confidence
MAX_CONSEC_LOSSES = 5       # at this -> skip (confidence 0.0)
CONFIDENCE_RECOVERY = 0.1   # per win, added back toward 1.0
HOUR_SAMPLE_MIN = 20        # trades needed before flagging an hour
ATR_HIGH_MULT = 2.0         # current > 2x avg -> scale down
ATR_LOW_MULT = 0.3          # current < 0.3x avg -> scale down
VOL_SCALE_FLOOR = 0.5       # minimum volatility scale factor


class AdaptiveGuard:
    """Defensive guard layer that learns from live performance.

    Usage from executor:
        guard = AdaptiveGuard()
        guard.load_state('live/state/adaptive.json')

        # Before entering a trade:
        confidence = guard.get_confidence(model)
        if not guard.is_safe_hour(current_hour):
            skip...
        vol_scale = guard.check_volatility(atr, avg_atr)
        effective_qty = max(1, int(base_qty * confidence * vol_scale))

        # Or use the combined convenience method:
        scale, guards = guard.pre_entry_check('ou_rev', 10, 18.5, 12.0)
        if scale == 0.0:
            skip signal
        else:
            qty = max(1, int(base_qty * scale))

        # After a trade closes:
        guard.record_trade('ou_rev', 0.85, 42.50, 10, 1.1)
        guard.save_state('live/state/adaptive.json')
    """

    def __init__(self):
        self._lock = threading.Lock()

        # Per-model rolling trades: model_name -> deque of TradeRecord
        self._trades: dict[str, deque] = {}

        # Per-model consecutive loss counter
        self._consec_losses: dict[str, int] = {}

        # Per-model confidence (starts at 1.0, only goes down defensively)
        self._confidence: dict[str, float] = {}

        # Hour-bucket P&L tracking: hour (0-23) -> list of pnl values
        self._hour_pnl: dict[int, list] = {h: [] for h in range(24)}

        # Session journal entries
        self._journal: list = []

    # ------------------------------------------------------------------
    # 1. Per-model rolling performance tracker
    # ------------------------------------------------------------------

    def record_trade(self, model: str, total_r: float, pnl_usd: float,
                     hour: int, atr_ratio: float) -> None:
        """Record a completed trade and update all internal state.

        Args:
            model: Model name (e.g. 'ou_rev', 'kalman_mom')
            total_r: Trade result in R-multiples
            pnl_usd: Trade result in USD
            hour: Hour of day (0-23, Chicago time)
            atr_ratio: current_atr / avg_atr at time of entry
        """
        now = datetime.utcnow().isoformat()
        rec = TradeRecord(
            timestamp=now,
            model=model,
            total_r=total_r,
            pnl_usd=pnl_usd,
            hour=hour,
            atr_ratio=atr_ratio,
        )

        with self._lock:
            # --- rolling trades ---
            if model not in self._trades:
                self._trades[model] = deque(maxlen=ROLLING_WINDOW)
            self._trades[model].append(rec)

            # --- consecutive losses ---
            if total_r <= -0.5:
                self._consec_losses[model] = self._consec_losses.get(model, 0) + 1
            else:
                self._consec_losses[model] = 0

            # --- confidence update ---
            self._update_confidence(model, rec.win)

            # --- hour bucket ---
            self._hour_pnl[hour].append(pnl_usd)

        # Log summary
        stats = self._model_stats(model)
        log.info(
            f"[AdaptiveGuard] Recorded {model}: {total_r:+.2f}R (${pnl_usd:+,.0f}) | "
            f"rolling WR={stats['win_rate']:.0%} PF={stats['profit_factor']:.2f} "
            f"avgR={stats['avg_r']:+.2f} | confidence={self._confidence.get(model, 1.0):.2f} | "
            f"consec_losses={self._consec_losses.get(model, 0)}"
        )

    def _update_confidence(self, model: str, was_win: bool) -> None:
        """Adjust model confidence based on latest result. Lock must be held."""
        cur = self._confidence.get(model, 1.0)
        consec = self._consec_losses.get(model, 0)
        trades = self._trades.get(model, deque())

        # Rule 1: 5+ consecutive losses -> SKIP
        if consec >= MAX_CONSEC_LOSSES:
            new = 0.0
            reason = f"{consec} consecutive losses -> SKIP"
        else:
            # Rule 2: rolling win rate below threshold -> half confidence
            if len(trades) >= 5:  # need a minimum sample
                wins = sum(1 for t in trades if t.win)
                wr = wins / len(trades)
                if wr < MIN_WIN_RATE:
                    new = 0.5
                    reason = f"rolling WR {wr:.0%} < {MIN_WIN_RATE:.0%} -> half size"
                elif was_win:
                    # Recovery: each win adds CONFIDENCE_RECOVERY back
                    new = min(1.0, cur + CONFIDENCE_RECOVERY)
                    reason = f"win recovery +{CONFIDENCE_RECOVERY} -> {new:.2f}"
                else:
                    new = cur  # no change on loss if above thresholds
                    reason = "loss but above thresholds, holding"
            elif was_win:
                new = min(1.0, cur + CONFIDENCE_RECOVERY)
                reason = f"win recovery +{CONFIDENCE_RECOVERY} -> {new:.2f}"
            else:
                new = cur
                reason = "loss, insufficient sample, holding"

        if new != cur:
            log.info(f"[AdaptiveGuard] {model} confidence {cur:.2f} -> {new:.2f}: {reason}")

        self._confidence[model] = new

    def _model_stats(self, model: str) -> dict:
        """Calculate rolling stats for a model. Lock must be held or called safely."""
        trades = self._trades.get(model, deque())
        if not trades:
            return {'win_rate': 0.0, 'profit_factor': 0.0, 'avg_r': 0.0, 'count': 0}

        wins = sum(1 for t in trades if t.win)
        gross_win = sum(t.total_r for t in trades if t.total_r > 0)
        gross_loss = abs(sum(t.total_r for t in trades if t.total_r <= 0))
        avg_r = sum(t.total_r for t in trades) / len(trades)

        return {
            'win_rate': wins / len(trades),
            'profit_factor': gross_win / gross_loss if gross_loss > 0 else 99.0,
            'avg_r': avg_r,
            'count': len(trades),
        }

    # ------------------------------------------------------------------
    # 2. Confidence scoring
    # ------------------------------------------------------------------

    def get_confidence(self, model: str) -> float:
        """Return 0.0-1.0 confidence multiplier for the model.

        - 1.0 = full backtest sizing (the max, never exceeded)
        - 0.5 = half size (rolling win rate below 25%)
        - 0.0 = SKIP this signal entirely (5+ consecutive losses)

        Recovers gradually: each win adds +0.1 back toward 1.0.
        """
        with self._lock:
            conf = self._confidence.get(model, 1.0)
            # Never scale up beyond 1.0
            conf = min(1.0, conf)

            reasons = []
            consec = self._consec_losses.get(model, 0)
            trades = self._trades.get(model, deque())

            if conf == 0.0:
                reasons.append(f"SKIP: {consec} consecutive losses")
            elif conf < 1.0:
                if len(trades) >= 5:
                    wr = sum(1 for t in trades if t.win) / len(trades)
                    reasons.append(f"degraded: WR={wr:.0%}")
                reasons.append(f"consec_losses={consec}")

            if reasons:
                log.info(f"[AdaptiveGuard] {model} confidence={conf:.2f} ({', '.join(reasons)})")
            else:
                log.debug(f"[AdaptiveGuard] {model} confidence={conf:.2f} (nominal)")

        return conf

    # ------------------------------------------------------------------
    # 3. Time-of-day guard
    # ------------------------------------------------------------------

    def is_safe_hour(self, hour: int) -> bool:
        """Check if trading in this hour has been historically profitable.

        Returns True (safe) until we have HOUR_SAMPLE_MIN+ trades in the
        bucket. After that, returns False if the hour is net negative P&L.
        """
        with self._lock:
            pnl_list = self._hour_pnl.get(hour, [])
            if len(pnl_list) < HOUR_SAMPLE_MIN:
                return True

            net = sum(pnl_list)
            if net < 0:
                log.info(
                    f"[AdaptiveGuard] Hour {hour}:00 UNSAFE -- "
                    f"{len(pnl_list)} trades, net ${net:+,.0f}"
                )
                return False

        return True

    # ------------------------------------------------------------------
    # 4. Volatility guard
    # ------------------------------------------------------------------

    @staticmethod
    def check_volatility(atr: float, avg_atr: float) -> float:
        """Return a 0.5-1.0 scaling factor based on current ATR vs average.

        - ATR > 2.0x average: too volatile, scale to 0.5
        - ATR < 0.3x average: too quiet (mean reversion won't work), scale to 0.5
        - Normal range (0.5x-1.5x): 1.0

        Smoothly interpolates in transition zones:
          High side: 1.0 at 1.5x, linearly down to 0.5 at 2.0x+
          Low side:  1.0 at 0.5x, linearly down to 0.5 at 0.3x-
        """
        if avg_atr <= 0:
            return 1.0

        ratio = atr / avg_atr
        scale = 1.0

        if ratio >= ATR_HIGH_MULT:
            scale = VOL_SCALE_FLOOR
            reason = f"ATR {ratio:.1f}x avg -- too volatile"
        elif ratio > 1.5:
            # Linear interpolation between 1.5x (1.0) and 2.0x (0.5)
            t = (ratio - 1.5) / (ATR_HIGH_MULT - 1.5)
            scale = 1.0 - t * (1.0 - VOL_SCALE_FLOOR)
            reason = f"ATR {ratio:.1f}x avg -- elevated"
        elif ratio <= ATR_LOW_MULT:
            scale = VOL_SCALE_FLOOR
            reason = f"ATR {ratio:.1f}x avg -- too quiet"
        elif ratio < 0.5:
            # Linear interpolation between 0.3x (0.5) and 0.5x (1.0)
            t = (0.5 - ratio) / (0.5 - ATR_LOW_MULT)
            scale = 1.0 - t * (1.0 - VOL_SCALE_FLOOR)
            reason = f"ATR {ratio:.1f}x avg -- subdued"
        else:
            reason = ""

        # Clamp
        scale = max(VOL_SCALE_FLOOR, min(1.0, scale))

        if scale < 1.0:
            log.info(f"[AdaptiveGuard] Volatility guard: scale={scale:.2f} ({reason})")

        return scale

    # ------------------------------------------------------------------
    # 5. Session journal
    # ------------------------------------------------------------------

    def add_journal_entry(self, model: str, direction: str, total_r: float,
                          pnl_usd: float, confidence: float, hour: int,
                          atr_ratio: float, guards_fired: list,
                          volatility_scale: float, final_scale: float) -> None:
        """Add a trade decision to the session journal for human review.

        Call this after a trade closes to record the full decision context
        including which guards were active at time of entry.
        """
        entry = JournalEntry(
            timestamp=datetime.utcnow().isoformat(),
            model=model,
            direction=direction,
            total_r=total_r,
            pnl_usd=pnl_usd,
            confidence=confidence,
            hour=hour,
            atr_ratio=atr_ratio,
            guards_fired=guards_fired,
            volatility_scale=volatility_scale,
            final_scale=final_scale,
        )
        with self._lock:
            self._journal.append(entry)

    def save_journal(self, filepath: str) -> None:
        """Write all journal entries to a JSON file (append-friendly).

        Loads existing entries from the file, merges new ones, then saves.
        This makes the journal accumulate across sessions.
        """
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        existing = []
        if path.exists():
            try:
                with open(path, 'r') as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, IOError):
                log.warning(
                    f"[AdaptiveGuard] Could not load existing journal "
                    f"at {filepath}, starting fresh"
                )
                existing = []

        with self._lock:
            new_entries = [e.to_dict() for e in self._journal]
            self._journal.clear()

        combined = existing + new_entries

        with open(path, 'w') as f:
            json.dump(combined, f, indent=2)

        log.info(
            f"[AdaptiveGuard] Journal saved: {len(new_entries)} new entries, "
            f"{len(combined)} total -> {filepath}"
        )

    # ------------------------------------------------------------------
    # 6. State persistence
    # ------------------------------------------------------------------

    def save_state(self, filepath: str) -> None:
        """Persist rolling performance data to JSON for restart continuity.

        Saves: per-model trades, consecutive loss counts, confidence scores,
        and hour-bucket P&L data.
        """
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            state = {
                'trades': {
                    model: [t.to_dict() for t in trades]
                    for model, trades in self._trades.items()
                },
                'consec_losses': dict(self._consec_losses),
                'confidence': dict(self._confidence),
                'hour_pnl': {
                    str(h): pnl_list
                    for h, pnl_list in self._hour_pnl.items()
                },
                'saved_at': datetime.utcnow().isoformat(),
            }

        with open(path, 'w') as f:
            json.dump(state, f, indent=2)

        total_trades = sum(len(t) for t in self._trades.values())
        log.info(
            f"[AdaptiveGuard] State saved: {len(self._trades)} models, "
            f"{total_trades} trades -> {filepath}"
        )

    def load_state(self, filepath: str) -> bool:
        """Load rolling performance data from JSON so the bot remembers
        across restarts.

        Returns True if loaded successfully, False otherwise (starts fresh).
        """
        path = Path(filepath)
        if not path.exists():
            log.info(f"[AdaptiveGuard] No saved state at {filepath} -- starting fresh")
            return False

        try:
            with open(path, 'r') as f:
                state = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            log.warning(f"[AdaptiveGuard] Failed to load state from {filepath}: {e}")
            return False

        with self._lock:
            # Restore trades
            self._trades.clear()
            for model, trade_dicts in state.get('trades', {}).items():
                dq = deque(maxlen=ROLLING_WINDOW)
                for td in trade_dicts:
                    try:
                        dq.append(TradeRecord.from_dict(td))
                    except (TypeError, KeyError) as e:
                        log.warning(
                            f"[AdaptiveGuard] Skipping malformed trade record: {e}"
                        )
                self._trades[model] = dq

            # Restore consecutive losses
            self._consec_losses = state.get('consec_losses', {})

            # Restore confidence
            self._confidence = state.get('confidence', {})

            # Restore hour P&L
            self._hour_pnl = {h: [] for h in range(24)}
            for h_str, pnl_list in state.get('hour_pnl', {}).items():
                try:
                    h = int(h_str)
                    if 0 <= h <= 23:
                        self._hour_pnl[h] = pnl_list
                except (ValueError, TypeError):
                    pass

        saved_at = state.get('saved_at', 'unknown')
        total_trades = sum(len(t) for t in self._trades.values())
        models_str = ', '.join(
            f"{m}(n={len(t)}, conf={self._confidence.get(m, 1.0):.2f})"
            for m, t in sorted(self._trades.items())
        )
        log.info(
            f"[AdaptiveGuard] State loaded from {filepath} (saved {saved_at}): "
            f"{total_trades} trades across {len(self._trades)} models"
        )
        if models_str:
            log.info(f"[AdaptiveGuard] Models: {models_str}")

        return True

    # ------------------------------------------------------------------
    # Convenience: combined pre-entry check
    # ------------------------------------------------------------------

    def pre_entry_check(self, model: str, hour: int,
                        atr: float, avg_atr: float) -> tuple:
        """Combined pre-entry guard check. Returns (scale_factor, guards_fired).

        scale_factor: 0.0 to 1.0 multiplier for qty (never exceeds 1.0)
        guards_fired: list of guard names that reduced the scale

        The executor can use this single call instead of calling each guard
        individually:
            scale, guards = guard.pre_entry_check('ou_rev', 10, 18.5, 12.0)
            if scale == 0.0:
                log.info("SKIP -- adaptive guard")
                return
            qty = max(1, int(base_qty * scale))
        """
        guards_fired = []
        scale = 1.0

        # Confidence guard
        confidence = self.get_confidence(model)
        if confidence < 1.0:
            guards_fired.append(f"confidence:{confidence:.2f}")
        scale *= confidence

        # Time-of-day guard
        if not self.is_safe_hour(hour):
            guards_fired.append(f"unsafe_hour:{hour}")
            scale = 0.0  # skip entirely

        # Volatility guard (only if not already skipping)
        if scale > 0.0:
            vol_scale = self.check_volatility(atr, avg_atr)
            if vol_scale < 1.0:
                guards_fired.append(f"volatility:{vol_scale:.2f}")
            scale *= vol_scale

        # Never exceed 1.0
        scale = min(1.0, scale)

        if guards_fired:
            log.info(
                f"[AdaptiveGuard] Pre-entry {model} @ hour {hour}: "
                f"scale={scale:.2f}, guards={guards_fired}"
            )
        else:
            log.debug(
                f"[AdaptiveGuard] Pre-entry {model} @ hour {hour}: "
                f"scale=1.0 (all clear)"
            )

        return scale, guards_fired


# ---------------------------------------------------------------------------
# Failure mode classification
# ---------------------------------------------------------------------------

class FailureMode(str, Enum):
    IMMEDIATE_STOP = 'immediate_stop'
    GAVE_BACK_PROFIT = 'gave_back_profit'
    WRONG_DIRECTION = 'wrong_direction'
    TARGET_TOO_FAR = 'target_too_far'
    VOL_SPIKE = 'vol_spike'
    BAD_REGIME = 'bad_regime'
    TIME_DECAY = 'time_decay'
    WINNER = 'winner'
    BREAKEVEN = 'breakeven'


class MarketRegime(str, Enum):
    TRENDING_UP = 'trending_up'
    TRENDING_DOWN = 'trending_down'
    RANGING = 'ranging'
    HIGH_VOL = 'high_vol'
    LOW_VOL = 'low_vol'


@dataclass
class TradeAutopsy:
    """Full post-trade analysis with failure classification."""
    timestamp: str
    model: str
    direction: str
    total_r: float
    pnl_usd: float
    failure_mode: str
    regime: str
    hour: int
    atr_ratio: float
    mfe_r: float
    hold_minutes: float
    risk_ticks: float
    lesson: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> 'TradeAutopsy':
        return cls(**d)


AUTOPSY_ROLLING = 50
REGIME_SAMPLE_MIN = 5
REGIME_BLOCK_THRESHOLD = -2.0


class PostTradeAnalyzer:
    """Classifies WHY trades fail and builds conditional filters.

    A wise trader journals every trade, identifies patterns in losses,
    and creates rules to avoid repeating them. This does it automatically.

    Failure modes:
      - immediate_stop: MFE < 0.3R, stopped out fast. Wrong entry or direction.
      - gave_back_profit: MFE > 1.0R but ended negative. Trail/target issue.
      - wrong_direction: MFE near zero, market went opposite. Signal was wrong.
      - target_too_far: MFE > 0.5R but < target, then reversed. Target too ambitious.
      - vol_spike: ATR > 1.5x avg at entry. Volatility killed the trade.
      - bad_regime: Model type vs market regime mismatch.
      - time_decay: Hit time stop, never got going.

    Conditional filters built from patterns:
      - model + regime → if cumulative R < threshold, block that combo
      - model + hour → already handled by AdaptiveGuard, but reinforced here
      - model + atr_bucket → block in specific volatility conditions
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._autopsies: deque[TradeAutopsy] = deque(maxlen=200)
        self._regime_scores: dict[str, float] = defaultdict(float)
        self._regime_counts: dict[str, int] = defaultdict(int)

    def analyze_trade(self, model: str, direction: str, total_r: float,
                      pnl_usd: float, mfe: float, risk: float,
                      risk_ticks: float, reason: str, atr_ratio: float,
                      hour: int, hold_seconds: float,
                      regime: str = 'unknown') -> TradeAutopsy:
        """Perform post-trade autopsy: classify failure and extract lesson."""

        mfe_r = mfe / risk if risk > 0 else 0.0
        hold_min = hold_seconds / 60.0

        if total_r > 0.3:
            failure_mode = FailureMode.WINNER
            lesson = f"Good trade. MFE {mfe_r:.1f}R captured."
        elif abs(total_r) <= 0.3:
            failure_mode = FailureMode.BREAKEVEN
            lesson = "Scratched. No clear edge on this entry."
        elif reason == 'time_stop':
            failure_mode = FailureMode.TIME_DECAY
            lesson = (f"Held {hold_min:.0f}m with no movement. "
                      f"Model {model} may not have edge in current conditions.")
        elif atr_ratio > 1.5:
            failure_mode = FailureMode.VOL_SPIKE
            lesson = (f"ATR was {atr_ratio:.1f}x average. "
                      f"Volatility too high for {model} sizing.")
        elif mfe_r < 0.3:
            if mfe_r < 0.1:
                failure_mode = FailureMode.WRONG_DIRECTION
                lesson = (f"MFE only {mfe_r:.2f}R — market went straight against. "
                          f"Signal quality was poor for {model} {direction}.")
            else:
                failure_mode = FailureMode.IMMEDIATE_STOP
                lesson = (f"MFE {mfe_r:.2f}R before stop. Entry timing was off. "
                          f"Consider: was {model} fighting the trend?")
        elif mfe_r >= 1.0:
            failure_mode = FailureMode.GAVE_BACK_PROFIT
            lesson = (f"Reached {mfe_r:.1f}R profit then reversed to {total_r:.2f}R. "
                      f"Trail stop or partial profit rules need review.")
        elif mfe_r >= 0.5:
            failure_mode = FailureMode.TARGET_TOO_FAR
            lesson = (f"MFE was {mfe_r:.1f}R but target required more. "
                      f"Smaller target or earlier partial would have saved this.")
        else:
            failure_mode = FailureMode.IMMEDIATE_STOP
            lesson = f"Stopped out with MFE {mfe_r:.2f}R."

        # Check regime mismatch
        if total_r < -0.3 and regime != 'unknown':
            is_mean_rev = 'rev' in model or 'ou' in model
            is_momentum = 'mom' in model or 'trend' in model or 'breakout' in model
            if is_mean_rev and regime in ('trending_up', 'trending_down'):
                failure_mode = FailureMode.BAD_REGIME
                lesson = (f"Mean-reversion model {model} traded in {regime} regime. "
                          f"Don't fade trends.")
            elif is_momentum and regime == 'ranging':
                failure_mode = FailureMode.BAD_REGIME
                lesson = (f"Momentum model {model} traded in ranging market. "
                          f"No trend to ride.")

        autopsy = TradeAutopsy(
            timestamp=datetime.utcnow().isoformat(),
            model=model,
            direction=direction,
            total_r=round(total_r, 3),
            pnl_usd=round(pnl_usd, 2),
            failure_mode=failure_mode.value,
            regime=regime,
            hour=hour,
            atr_ratio=round(atr_ratio, 3),
            mfe_r=round(mfe_r, 3),
            hold_minutes=round(hold_min, 1),
            risk_ticks=risk_ticks,
            lesson=lesson,
        )

        with self._lock:
            self._autopsies.append(autopsy)
            combo_key = f"{model}|{regime}"
            self._regime_scores[combo_key] += total_r
            self._regime_counts[combo_key] += 1

        if total_r < -0.3:
            log.warning(
                f"[PostTradeAnalyzer] AUTOPSY [{failure_mode.value}] {model} "
                f"{direction}: {total_r:+.2f}R | MFE={mfe_r:.1f}R | "
                f"regime={regime} | {lesson}"
            )
        else:
            log.info(
                f"[PostTradeAnalyzer] {model} {direction}: {total_r:+.2f}R | "
                f"MFE={mfe_r:.1f}R | {failure_mode.value}"
            )

        return autopsy

    def should_block_regime(self, model: str, regime: str) -> tuple[bool, str]:
        """Check if model+regime combo has been losing. Returns (block, reason)."""
        combo_key = f"{model}|{regime}"
        with self._lock:
            count = self._regime_counts.get(combo_key, 0)
            if count < REGIME_SAMPLE_MIN:
                return False, ''
            cum_r = self._regime_scores.get(combo_key, 0.0)
            avg_r = cum_r / count
            if cum_r < REGIME_BLOCK_THRESHOLD:
                reason = (f"{model} in {regime}: {count} trades, "
                          f"cumR={cum_r:+.1f}, avgR={avg_r:+.2f}")
                log.info(f"[PostTradeAnalyzer] BLOCKING {reason}")
                return True, reason
        return False, ''

    def get_recent_failure_pattern(self, model: str, n: int = 5) -> str | None:
        """If last N trades of a model share the same failure mode, flag it."""
        with self._lock:
            model_trades = [a for a in self._autopsies
                            if a.model == model and a.failure_mode not in
                            (FailureMode.WINNER.value, FailureMode.BREAKEVEN.value)]
            if len(model_trades) < n:
                return None
            recent = list(model_trades)[-n:]
            modes = [t.failure_mode for t in recent]
            if len(set(modes)) == 1:
                return (f"{model} has {n} consecutive {modes[0]} failures. "
                        f"Recurring pattern detected.")
        return None

    def get_model_summary(self) -> dict[str, dict]:
        """Return per-model failure mode breakdown for reporting."""
        with self._lock:
            summary = defaultdict(lambda: defaultdict(int))
            for a in self._autopsies:
                summary[a.model][a.failure_mode] += 1
            return {m: dict(modes) for m, modes in summary.items()}

    def save_state(self, filepath: str) -> None:
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            state = {
                'autopsies': [a.to_dict() for a in self._autopsies],
                'regime_scores': dict(self._regime_scores),
                'regime_counts': dict(self._regime_counts),
                'saved_at': datetime.utcnow().isoformat(),
            }
        with open(path, 'w') as f:
            json.dump(state, f, indent=2)
        log.info(f"[PostTradeAnalyzer] State saved: {len(self._autopsies)} autopsies -> {filepath}")

    def load_state(self, filepath: str) -> bool:
        path = Path(filepath)
        if not path.exists():
            log.info(f"[PostTradeAnalyzer] No saved state at {filepath}")
            return False
        try:
            with open(path, 'r') as f:
                state = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            log.warning(f"[PostTradeAnalyzer] Failed to load {filepath}: {e}")
            return False
        with self._lock:
            self._autopsies.clear()
            for d in state.get('autopsies', []):
                try:
                    self._autopsies.append(TradeAutopsy.from_dict(d))
                except (TypeError, KeyError):
                    pass
            self._regime_scores = defaultdict(float, state.get('regime_scores', {}))
            self._regime_counts = defaultdict(int, state.get('regime_counts', {}))
        log.info(f"[PostTradeAnalyzer] Loaded {len(self._autopsies)} autopsies from {filepath}")
        return True
