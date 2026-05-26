#!/usr/bin/env python3

from __future__ import annotations

import argparse

import pandas as pd

try:
    from pyscripts.common import load_stock_data, validate_day_counts
except ModuleNotFoundError:
    from common import load_stock_data, validate_day_counts


def _wilder_smooth(values: pd.Series, day_count: int) -> pd.Series:
    smoothed = pd.Series(index=values.index, dtype=float)
    first_valid_index = day_count - 1
    smoothed.iloc[first_valid_index] = float(values.iloc[:day_count].mean())

    for index in range(day_count, len(values)):
        smoothed.iloc[index] = (
            (float(smoothed.iloc[index - 1]) * (day_count - 1)) + float(values.iloc[index])
        ) / day_count

    return smoothed


def _calculate_adx(df: pd.DataFrame, day_count: int) -> dict[str, float]:
    if len(df) < (2 * day_count):
        raise ValueError(f"Not enough data to compute ADX({day_count})")

    high_diff = df["high"].diff()
    low_diff = -df["low"].diff()

    plus_dm = high_diff.where((high_diff > low_diff) & (high_diff > 0), 0.0)
    minus_dm = low_diff.where((low_diff > high_diff) & (low_diff > 0), 0.0)

    previous_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    plus_dm = plus_dm.iloc[1:].reset_index(drop=True)
    minus_dm = minus_dm.iloc[1:].reset_index(drop=True)
    true_range = true_range.iloc[1:].reset_index(drop=True)

    smoothed_plus_dm = _wilder_smooth(plus_dm, day_count)
    smoothed_minus_dm = _wilder_smooth(minus_dm, day_count)
    smoothed_true_range = _wilder_smooth(true_range, day_count)

    plus_di = 100.0 * smoothed_plus_dm / smoothed_true_range
    minus_di = 100.0 * smoothed_minus_dm / smoothed_true_range

    di_sum = plus_di + minus_di
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    dx = dx.where(di_sum != 0.0, 0.0)
    dx = dx.iloc[day_count - 1 :].reset_index(drop=True)

    adx_series = _wilder_smooth(dx, day_count)
    last_adx_index = adx_series.last_valid_index()
    if last_adx_index is None:
        raise ValueError(f"Not enough data to compute ADX({day_count})")

    di_index = last_adx_index + day_count - 1
    return {
        "adx": float(adx_series.iloc[last_adx_index]),
        "plus_di": float(plus_di.iloc[di_index]),
        "minus_di": float(minus_di.iloc[di_index]),
    }


def return_adx(symbol: str, day_counts: list[int], as_of_date: str) -> dict[int, dict[str, float]]:
    validated_day_counts = validate_day_counts(day_counts)
    normalized_symbol, df, resolved_date = load_stock_data(symbol, as_of_date, ["high", "low", "close"])

    adx_values: dict[int, dict[str, float]] = {}
    usable_rows = len(df)
    for day_count in validated_day_counts:
        if usable_rows < (2 * day_count):
            raise ValueError(
                f"Not enough data to compute ADX({day_count}) for {normalized_symbol} "
                f"through {resolved_date.date().isoformat()}"
            )
        adx_values[day_count] = _calculate_adx(df, day_count)

    return adx_values


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest ADX values for a stock symbol")
    parser.add_argument("symbol", help="Stock symbol from stocks.txt")
    parser.add_argument("day_counts", nargs="+", type=int, help="ADX day counts to calculate")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(return_adx(args.symbol, args.day_counts, args.as_of_date))


if __name__ == "__main__":
    main()
