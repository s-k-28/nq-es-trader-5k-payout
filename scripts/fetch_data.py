#!/usr/bin/env python3
"""Fetch real NQ/ES futures data from Yahoo Finance (free, no API key)."""
import yfinance as yf
import pandas as pd
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')


def fetch(symbol: str, yf_ticker: str, interval: str, period: str) -> pd.DataFrame:
    print(f"Fetching {symbol} ({yf_ticker}) interval={interval} period={period}...")
    tk = yf.Ticker(yf_ticker)
    df = tk.history(period=period, interval=interval, prepost=True)

    if df.empty:
        raise RuntimeError(f"No data returned for {yf_ticker}")

    df = df.reset_index()
    col_map = {}
    for c in df.columns:
        cl = c.lower()
        if 'datetime' in cl or 'date' in cl:
            col_map[c] = 'datetime'
        elif cl == 'open':
            col_map[c] = 'open'
        elif cl == 'high':
            col_map[c] = 'high'
        elif cl == 'low':
            col_map[c] = 'low'
        elif cl == 'close':
            col_map[c] = 'close'
        elif cl == 'volume':
            col_map[c] = 'volume'

    df = df.rename(columns=col_map)
    df['datetime'] = pd.to_datetime(df['datetime'])

    if df['datetime'].dt.tz is not None:
        df['datetime'] = df['datetime'].dt.tz_convert('US/Eastern').dt.tz_localize(None)

    df = df[['datetime', 'open', 'high', 'low', 'close', 'volume']].copy()
    df = df.sort_values('datetime').reset_index(drop=True)
    df['symbol'] = symbol
    return df


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    # 2-min bars, 60 days — best free resolution with decent history
    for symbol, ticker in [('NQ', 'NQ=F'), ('ES', 'ES=F')]:
        df = fetch(symbol, ticker, interval='2m', period='60d')
        path = os.path.join(DATA_DIR, f'{symbol}_2min.csv')
        df.to_csv(path, index=False)
        print(f"  {len(df):,} bars → {path}")
        print(f"  Range: {df['datetime'].min()} → {df['datetime'].max()}")

    # also grab 5-min for longer history validation
    for symbol, ticker in [('NQ', 'NQ=F'), ('ES', 'ES=F')]:
        df = fetch(symbol, ticker, interval='5m', period='60d')
        path = os.path.join(DATA_DIR, f'{symbol}_5min.csv')
        df.to_csv(path, index=False)
        print(f"  {len(df):,} bars → {path}")

    print("\nDone. Run backtest with:")
    print("  python3 run_backtest.py --nq data/NQ_2min.csv --es data/ES_2min.csv --plot equity.png")


if __name__ == '__main__':
    main()
