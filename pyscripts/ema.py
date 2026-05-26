#!/usr/bin/env python3

from __future__ import annotations

import argparse

import pandas as pd

try:
    from pyscripts.common import load_stock_data, validate_day_counts
except ModuleNotFoundError:
    from common import load_stock_data, validate_day_counts


def return_ema(symbol: str, day_counts: list[int], as_of_date: str) -> dict[int, float]:
    validated_day_counts = validate_day_counts(day_counts)
    normalized_symbol, df, resolved_date = load_stock_data(symbol, as_of_date, ["close"])

    close_series = df["close"]

    ema_values: dict[int, float] = {}
    usable_rows = len(close_series)
    for day_count in validated_day_counts:
        if usable_rows < day_count:
            raise ValueError(
                f"Not enough data to compute EMA({day_count}) for {normalized_symbol} "
                f"through {resolved_date.date().isoformat()}"
            )
        ema_series = close_series.ewm(span=day_count, adjust=False, min_periods=day_count).mean()
        ema_values[day_count] = float(ema_series.iloc[-1])

    return ema_values


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest EMA values for a stock symbol")
    parser.add_argument("symbol", help="Stock symbol from stocks.txt")
    parser.add_argument("day_counts", nargs="+", type=int, help="EMA day counts to calculate")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(return_ema(args.symbol, args.day_counts, args.as_of_date))


if __name__ == "__main__":
    main()
