#!/usr/bin/env python3

from __future__ import annotations

import argparse

import pandas as pd

try:
    from pyscripts.common import load_stock_data, validate_day_counts
except ModuleNotFoundError:
    from common import load_stock_data, validate_day_counts


def _rolling_k(df: pd.DataFrame, day_count: int) -> pd.Series:
    highest_high = df["high"].rolling(window=day_count, min_periods=day_count).max()
    lowest_low = df["low"].rolling(window=day_count, min_periods=day_count).min()
    price_range = highest_high - lowest_low
    raw_k = 100.0 * (df["close"] - lowest_low) / price_range
    return raw_k.where(price_range != 0.0, 0.0)


def return_stochastics_kd(
    symbol: str,
    k_day_counts: list[int],
    as_of_date: str,
    smooth_k_day_count: int = 3,
    d_day_count: int = 3,
) -> dict[int, dict[str, float]]:
    validated_day_counts = validate_day_counts(k_day_counts, label="k_day_counts")
    if smooth_k_day_count <= 0 or d_day_count <= 0:
        raise ValueError("smooth_k_day_count and d_day_count must be positive integers")

    normalized_symbol, df, resolved_date = load_stock_data(symbol, as_of_date, ["high", "low", "close"])

    results: dict[int, dict[str, float]] = {}
    usable_rows = len(df)
    for day_count in validated_day_counts:
        min_rows = day_count + smooth_k_day_count + d_day_count - 2
        if usable_rows < min_rows:
            raise ValueError(
                f"Not enough data to compute Stochastics KD({day_count},{smooth_k_day_count},{d_day_count}) "
                f"for {normalized_symbol} through {resolved_date.date().isoformat()}"
            )

        raw_k = _rolling_k(df, day_count)
        smoothed_k = raw_k.rolling(window=smooth_k_day_count, min_periods=smooth_k_day_count).mean()
        d_line = smoothed_k.rolling(window=d_day_count, min_periods=d_day_count).mean()

        last_index = d_line.last_valid_index()
        if last_index is None:
            raise ValueError(
                f"Not enough data to compute Stochastics KD({day_count},{smooth_k_day_count},{d_day_count})"
            )

        results[day_count] = {
            "k": float(smoothed_k.loc[last_index]),
            "d": float(d_line.loc[last_index]),
        }

    return results


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest Stochastics KD values for a stock symbol")
    parser.add_argument("symbol", help="Stock symbol from stocks.txt")
    parser.add_argument("k_day_counts", nargs="+", type=int, help="%%K day counts to calculate")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    parser.add_argument("--smooth-k", dest="smooth_k_day_count", type=int, default=3, help="Smoothing window for %%%%K")
    parser.add_argument("--d-period", dest="d_day_count", type=int, default=3, help="Smoothing window for %%%%D")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(
        return_stochastics_kd(
            args.symbol,
            args.k_day_counts,
            args.as_of_date,
            smooth_k_day_count=args.smooth_k_day_count,
            d_day_count=args.d_day_count,
        )
    )


if __name__ == "__main__":
    main()
