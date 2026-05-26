#!/usr/bin/env python3

from __future__ import annotations

import argparse

import pandas as pd

try:
    from pyscripts.common import load_stock_data, validate_day_counts
except ModuleNotFoundError:
    from common import load_stock_data, validate_day_counts


def _build_band_values(close_series: pd.Series, middle_series: pd.Series, std_series: pd.Series, std_multiplier: float) -> dict[str, float]:
    last_index = middle_series.last_valid_index()
    if last_index is None:
        raise ValueError("Not enough data to compute Bollinger Bands")

    middle = float(middle_series.loc[last_index])
    std_value = float(std_series.loc[last_index])
    close_value = float(close_series.loc[last_index])
    upper = middle + (std_multiplier * std_value)
    lower = middle - (std_multiplier * std_value)
    band_span = upper - lower

    if middle == 0.0:
        bandwidth = 0.0
    else:
        bandwidth = (band_span / middle) * 100.0

    if band_span == 0.0:
        percent_b = 0.0
    else:
        percent_b = (close_value - lower) / band_span

    return {
        "upper": upper,
        "middle": middle,
        "lower": lower,
        "bandwidth": bandwidth,
        "percent_b": percent_b,
    }


def return_bollinger_bands(
    symbol: str,
    day_counts: list[int],
    as_of_date: str,
    std_multiplier: float = 2.0,
) -> dict[int, dict[str, dict[str, float]]]:
    if std_multiplier <= 0:
        raise ValueError("std_multiplier must be greater than 0")

    validated_day_counts = validate_day_counts(day_counts)
    normalized_symbol, df, resolved_date = load_stock_data(symbol, as_of_date, ["close"])
    close_series = df["close"]

    band_values: dict[int, dict[str, dict[str, float]]] = {}
    usable_rows = len(close_series)
    for day_count in validated_day_counts:
        if usable_rows < day_count:
            raise ValueError(
                f"Not enough data to compute Bollinger Bands({day_count}) for {normalized_symbol} "
                f"through {resolved_date.date().isoformat()}"
            )

        std_series = close_series.rolling(window=day_count, min_periods=day_count).std(ddof=0)
        sma_middle = close_series.rolling(window=day_count, min_periods=day_count).mean()
        ema_middle = close_series.ewm(span=day_count, adjust=False, min_periods=day_count).mean()

        band_values[day_count] = {
            "sma": _build_band_values(close_series, sma_middle, std_series, std_multiplier),
            "ema": _build_band_values(close_series, ema_middle, std_series, std_multiplier),
        }

    return band_values


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest Bollinger Bands values for a stock symbol")
    parser.add_argument("symbol", help="Stock symbol from stocks.txt")
    parser.add_argument("day_counts", nargs="+", type=int, help="Bollinger Bands day counts to calculate")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    parser.add_argument("--std-multiplier", type=float, default=2.0, help="Standard deviation multiplier")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(return_bollinger_bands(args.symbol, args.day_counts, args.as_of_date, std_multiplier=args.std_multiplier))


if __name__ == "__main__":
    main()
