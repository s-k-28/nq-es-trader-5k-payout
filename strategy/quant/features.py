"""Quantitative feature engine for NQ intraday trading.

Implements hedge-fund-grade mathematical models:
- Ornstein-Uhlenbeck process: mean-reversion speed, half-life, z-score
- Hurst exponent: trending vs mean-reverting regime classification
- Kalman filter: optimal recursive level + slope estimation
- Parkinson estimator: high-low range volatility (more efficient than close-to-close)
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def compute_ou_params(df: pd.DataFrame, window: int = 60) -> pd.DataFrame:
    """Estimate Ornstein-Uhlenbeck process on price-VWAP deviation.

    dX = theta*(mu - X)*dt + sigma*dW

    Uses rolling OLS:  dX(t) = alpha + beta*X(t-1) + eps
    theta = -beta,  half_life = ln2 / theta
    z_score = X / sigma_equilibrium
    """
    if 'vwap' not in df.columns:
        return df

    X = (df['close'] - df['vwap']).values.astype(float)
    dX = np.empty_like(X)
    dX[0] = np.nan
    dX[1:] = X[1:] - X[:-1]

    X_lag = np.empty_like(X)
    X_lag[0] = np.nan
    X_lag[1:] = X[:-1]

    s_dX = pd.Series(dX, index=df.index)
    s_Xlag = pd.Series(X_lag, index=df.index)
    s_prod = s_dX * s_Xlag

    kw = dict(window=window, min_periods=30)
    roll_mean_dX = s_dX.rolling(**kw).mean()
    roll_mean_Xlag = s_Xlag.rolling(**kw).mean()
    roll_mean_prod = s_prod.rolling(**kw).mean()
    roll_var_Xlag = s_Xlag.rolling(**kw).var(ddof=0)

    cov = roll_mean_prod - roll_mean_dX * roll_mean_Xlag
    beta = (cov / roll_var_Xlag.replace(0, np.nan)).values

    theta = -beta
    half_life = np.where(theta > 0, np.log(2) / theta, np.nan)

    residuals = dX - beta * X_lag
    res_std = pd.Series(residuals, index=df.index).rolling(**kw).std().values
    sigma_eq = np.where(theta > 0, res_std / np.sqrt(np.maximum(2 * theta, 1e-10)), np.nan)
    z_score = np.where(
        (sigma_eq is not None) & (sigma_eq > 0) & np.isfinite(sigma_eq),
        X / sigma_eq, 0.0
    )

    df = df.copy()
    df['ou_theta'] = theta
    df['ou_half_life'] = half_life
    df['ou_zscore'] = z_score
    return df


def compute_hurst(df: pd.DataFrame, window: int = 120) -> pd.DataFrame:
    """Rolling Hurst exponent via variance-ratio method.

    H = log(Var[r_tau] / Var[r_1]) / (2*log(tau))

    H < 0.45 -> mean-reverting     (fade extremes)
    H > 0.55 -> trending           (follow momentum)
    H ~ 0.50 -> random walk        (sit out)
    """
    close = df['close']
    ret1 = close.diff(1)
    ret16 = close.diff(16)

    kw = dict(window=window, min_periods=40)
    var1 = ret1.rolling(**kw).var()
    var16 = ret16.rolling(**kw).var()

    ratio = var16 / var1.replace(0, np.nan)
    hurst = np.log(ratio.clip(lower=1e-10)) / (2 * np.log(16))

    df = df.copy()
    df['hurst'] = hurst.values
    return df


def compute_kalman(df: pd.DataFrame,
                   q_level: float = 1.0,
                   q_slope: float = 0.01,
                   r_obs: float = 2.0) -> pd.DataFrame:
    """Kalman filter with state = [level, slope].

    Transition:  level(t) = level(t-1) + slope(t-1)
                 slope(t) = slope(t-1)
    Observation: price(t) = level(t) + noise

    Inline 2x2 math for speed on 1M+ bars.
    """
    prices = df['close'].values.astype(float)
    n = len(prices)
    level = np.empty(n)
    slope = np.empty(n)

    x0 = prices[0]
    x1 = 0.0
    p00, p01, p11 = 100.0, 0.0, 1.0

    level[0] = x0
    slope[0] = x1

    for i in range(1, n):
        xp0 = x0 + x1
        xp1 = x1
        pp00 = p00 + 2 * p01 + p11 + q_level
        pp01 = p01 + p11
        pp11 = p11 + q_slope

        y = prices[i] - xp0
        s_inv = 1.0 / (pp00 + r_obs)
        k0 = pp00 * s_inv
        k1 = pp01 * s_inv

        x0 = xp0 + k0 * y
        x1 = xp1 + k1 * y
        p00 = (1 - k0) * pp00
        p01 = (1 - k0) * pp01
        p11 = pp11 - k1 * pp01

        level[i] = x0
        slope[i] = x1

    df = df.copy()
    df['kalman_level'] = level
    df['kalman_slope'] = slope
    return df


def compute_parkinson_vol(df: pd.DataFrame, window: int = 30) -> pd.DataFrame:
    """Parkinson high-low volatility estimator.

    sigma^2 = 1/(4*ln2) * E[(ln(H/L))^2]

    More statistically efficient than close-to-close: uses full intrabar range.
    """
    log_hl = np.log(df['high'] / df['low'].replace(0, np.nan))
    log_hl_sq = log_hl ** 2
    park_vol = np.sqrt(log_hl_sq.rolling(window, min_periods=10).mean() / (4 * np.log(2)))

    df = df.copy()
    df['park_vol'] = park_vol.values
    return df


def compute_bb_squeeze(df: pd.DataFrame, period: int = 20, std: float = 2.0,
                       pctile_window: int = 120) -> pd.DataFrame:
    """Bollinger Band Width and squeeze detection.

    BBW = (upper - lower) / middle
    Squeeze = BBW below 10th percentile of recent history.
    """
    mid = df['close'].rolling(period, min_periods=period).mean()
    sd = df['close'].rolling(period, min_periods=period).std()
    upper = mid + std * sd
    lower = mid - std * sd
    bbw = (upper - lower) / mid.replace(0, np.nan)

    bbw_pctile = bbw.rolling(pctile_window, min_periods=40).apply(
        lambda x: (x.iloc[-1] <= x).sum() / len(x) * 100, raw=False
    )

    df = df.copy()
    df['bb_mid'] = mid.values
    df['bb_upper'] = upper.values
    df['bb_lower'] = lower.values
    df['bbw'] = bbw.values
    df['bbw_pctile'] = bbw_pctile.values
    return df


def compute_atr(df: pd.DataFrame, period: int = 5) -> pd.DataFrame:
    high = df['high']
    low = df['low']
    prev_close = df['close'].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period, min_periods=period).mean()
    df = df.copy()
    df[f'atr_{period}'] = atr.values
    return df


def compute_all_quant_features(df: pd.DataFrame) -> pd.DataFrame:
    df = compute_ou_params(df)
    df = compute_hurst(df)
    df = compute_kalman(df)
    df = compute_parkinson_vol(df)
    df = compute_bb_squeeze(df)
    df = compute_atr(df, period=5)
    return df
