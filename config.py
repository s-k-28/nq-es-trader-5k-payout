from dataclasses import dataclass, field
from datetime import time


@dataclass
class InstrumentConfig:
    symbol: str = "NQ"
    tick_size: float = 0.25
    tick_value: float = 5.0
    point_value: float = 20.0
    # Live-execution instrument (the live bot trades MNQ, the micro contract).
    # These are intentionally distinct from the NQ tick_value/point_value above
    # so the live executor's sizing/PnL math stays MNQ-correct ($0.50/tick,
    # $2/point) while backtest/regime code continues to use the NQ values.
    live_tick_value: float = 0.50
    live_point_value: float = 2.0


@dataclass
class SessionTimes:
    overnight_start: time = field(default_factory=lambda: time(18, 0))
    overnight_end: time = field(default_factory=lambda: time(9, 30))
    rth_start: time = field(default_factory=lambda: time(9, 30))
    rth_end: time = field(default_factory=lambda: time(16, 0))
    london_kz_start: time = field(default_factory=lambda: time(3, 0))
    london_kz_end: time = field(default_factory=lambda: time(5, 0))
    ny_am_kz_start: time = field(default_factory=lambda: time(9, 30))
    ny_am_kz_end: time = field(default_factory=lambda: time(11, 0))
    session_close: time = field(default_factory=lambda: time(16, 59))


@dataclass
class StrategyParams:
    ema_period: int = 20
    bias_min_agreement: int = 2
    atr_period: int = 14
    atr_expansion_lookback: int = 5
    bbw_period: int = 20
    bbw_std: float = 2.0
    swing_lookback: int = 10
    equal_level_tolerance_ticks: int = 3
    sweep_min_ticks: int = 2
    sweep_max_candles: int = 3
    mss_body_ratio: float = 0.6
    mss_min_ticks: int = 8
    fvg_min_ticks: int = 4
    fvg_entry_pct: float = 0.5
    fvg_max_wait_candles: int = 10
    use_intermarket: bool = True
    time_stop_minutes: int = 45
    atr_stop_length: int = 5
    atr_stop_factor: float = 0.80


@dataclass
class RiskParams:
    risk_per_trade_pct: float = 1.0
    max_daily_losses: int = 2
    max_daily_loss_r: float = 999.0
    stop_buffer_ticks: int = 3
    min_risk_ticks: int = 40
    max_risk_ticks: int = 200
    # System-wide hard cap on contracts per position (matches backtest + paper).
    max_contracts: int = 20
    be_trigger_rr: float = 0.8
    partial_rr: float = 0.5
    partial_pct: float = 0.0
    target_rr: float = 3.0
    min_rr: float = 2.0
    max_concurrent: int = 1
    consec_loss_cooldown: int = 10
    no_friday_pm: bool = True


@dataclass
class FundedAccountParams:
    eval_profit_target: float = 6000.0
    trailing_dd: float = 3000.0
    static_threshold: float = 3000.0
    green_day_min: float = 150.0
    max_payout: float = 5000.0
    payout_balance_pct: float = 0.30
    green_days_per_payout: int = 5
    dollar_loss_cap: float = 1000.0
    daily_profit_target: float = 800.0
    green_day_protect: float = 250.0
    green_day_protect_scale: float = 0.5
    daily_win_cap_r: float = 3.0
    # Hard per-trade loss backstop: force-flatten if a single trade's open loss
    # exceeds intended_risk_dollars * this multiple (bounded by remaining DLC).
    # Catches stop-market slippage / overruns that breach the assumed 1R risk.
    per_trade_hard_stop_mult: float = 1.5
    post_static_scaling: list = field(default_factory=lambda: [
        (3000, 1.0), (0, 1.0),
    ])
    model_risk_dollars: dict = field(default_factory=lambda: {
        'ou_rev': 500, 'pd_rev': 500,
        'vwap_rev': 500, 'or_rev': 500, 'ou_lunch': 400,
        'vwap_scalp': 400, 'open_drive': 600,
        'trend': 600, 'pm_mom': 400,
        'ema_rev': 400, 'kalman_mom': 400, 'sweep': 400,
    })


@dataclass
class Config:
    instrument: InstrumentConfig = field(default_factory=InstrumentConfig)
    sessions: SessionTimes = field(default_factory=SessionTimes)
    strategy: StrategyParams = field(default_factory=StrategyParams)
    risk: RiskParams = field(default_factory=RiskParams)
    funded: FundedAccountParams = field(default_factory=FundedAccountParams)
    account_size: float = 100000.0
