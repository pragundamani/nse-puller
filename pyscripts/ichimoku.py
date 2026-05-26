#!/usr/bin/env python3

from __future__ import annotations

import argparse

import pandas as pd

try:
    from pyscripts.common import DEFAULT_STOCKS_DIR, normalize_symbol, resolve_as_of_date
except ModuleNotFoundError:
    from common import DEFAULT_STOCKS_DIR, normalize_symbol, resolve_as_of_date


def return_ichimoku(
    symbol: str,
    as_of_date: str,
    conversion_day_count: int = 9,
    base_day_count: int = 26,
    span_b_day_count: int = 52,
) -> dict[str, float]:
    if min(conversion_day_count, base_day_count, span_b_day_count) <= 0:
        raise ValueError("Ichimoku periods must be positive integers")
    if not (conversion_day_count <= base_day_count <= span_b_day_count):
        raise ValueError("Ichimoku periods should satisfy conversion <= base <= span_b")

    displacement = base_day_count
    normalized_symbol = normalize_symbol(symbol)
    stock_file = DEFAULT_STOCKS_DIR / f"{normalized_symbol}.csv"
    if not stock_file.exists():
        raise FileNotFoundError(f"Stock data file not found: {stock_file}")

    df = pd.read_csv(stock_file, usecols=["date", "high", "low", "close"])
    if df.empty:
        raise ValueError(f"No stock data found in {stock_file}")
    df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d", errors="raise")
    df = df.sort_values("date").reset_index(drop=True)
    for column in ["high", "low", "close"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
        if df[column].isna().any():
            raise ValueError(f"Invalid {column} values found in {stock_file}")

    resolved_date = resolve_as_of_date(df["date"], as_of_date)
    resolved_index = int(df.index[df["date"] == resolved_date][0])

    if resolved_index + 1 < span_b_day_count:
        raise ValueError(
            f"Not enough data to compute Ichimoku for {normalized_symbol} through {resolved_date.date().isoformat()}"
        )
    if resolved_index + 1 <= displacement:
        raise ValueError(
            f"Not enough data to compute Ichimoku chikou span for {normalized_symbol} through {resolved_date.date().isoformat()}"
        )
    if resolved_index < displacement + span_b_day_count - 1:
        raise ValueError(
            f"Not enough history to align Ichimoku cloud at {resolved_date.date().isoformat()}"
        )
    if resolved_index + displacement >= len(df):
        raise ValueError(
            f"Not enough future data to align Ichimoku chikou span at {resolved_date.date().isoformat()}"
        )

    high = df["high"]
    low = df["low"]
    close = df["close"]
    tenkan = (high.rolling(conversion_day_count).max() + low.rolling(conversion_day_count).min()) / 2.0
    kijun = (high.rolling(base_day_count).max() + low.rolling(base_day_count).min()) / 2.0
    senkou_a_unshifted = (tenkan + kijun) / 2.0
    senkou_b_unshifted = (high.rolling(span_b_day_count).max() + low.rolling(span_b_day_count).min()) / 2.0

    senkou_a = senkou_a_unshifted.shift(displacement)
    senkou_b = senkou_b_unshifted.shift(displacement)
    chikou_span = close.shift(-displacement)

    final_tenkan = float(tenkan.iloc[resolved_index])
    final_kijun = float(kijun.iloc[resolved_index])
    final_senkou_a = float(senkou_a.iloc[resolved_index])
    final_senkou_b = float(senkou_b.iloc[resolved_index])
    final_chikou = float(chikou_span.iloc[resolved_index])

    return {
        "tenkan_sen": final_tenkan,
        "kijun_sen": final_kijun,
        "senkou_span_a": final_senkou_a,
        "senkou_span_b": final_senkou_b,
        "cloud_top": max(final_senkou_a, final_senkou_b),
        "cloud_bottom": min(final_senkou_a, final_senkou_b),
        "chikou_span": final_chikou,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest Ichimoku values for a stock symbol")
    parser.add_argument("symbol", help="Stock symbol from stocks.txt")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    parser.add_argument("--conversion", dest="conversion_day_count", type=int, default=9, help="Conversion line period")
    parser.add_argument("--base", dest="base_day_count", type=int, default=26, help="Base line period and displacement")
    parser.add_argument("--span-b", dest="span_b_day_count", type=int, default=52, help="Span B period")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(
        return_ichimoku(
            args.symbol,
            args.as_of_date,
            conversion_day_count=args.conversion_day_count,
            base_day_count=args.base_day_count,
            span_b_day_count=args.span_b_day_count,
        )
    )


if __name__ == "__main__":
    main()
