#!/usr/bin/env python3

from __future__ import annotations

import argparse

try:
    from pyscripts.common import load_stock_data, validate_day_counts
except ModuleNotFoundError:
    from common import load_stock_data, validate_day_counts


def return_williams_r(symbol: str, day_counts: list[int], as_of_date: str) -> dict[int, float]:
    validated_day_counts = validate_day_counts(day_counts)
    normalized_symbol, df, resolved_date = load_stock_data(symbol, as_of_date, ["high", "low", "close"])

    williams_values: dict[int, float] = {}
    for day_count in validated_day_counts:
        if len(df) < day_count:
            raise ValueError(
                f"Not enough data to compute Williams %R({day_count}) for {normalized_symbol} "
                f"through {resolved_date.date().isoformat()}"
            )

        window = df.iloc[-day_count:]
        highest_high = float(window["high"].max())
        lowest_low = float(window["low"].min())
        close_value = float(window["close"].iloc[-1])
        price_range = highest_high - lowest_low
        if price_range == 0.0:
            williams_values[day_count] = 0.0
            continue
        williams_values[day_count] = -100.0 * ((highest_high - close_value) / price_range)

    return williams_values


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest Williams %R values for a stock symbol")
    parser.add_argument("symbol", help="Stock symbol from stocks.txt")
    parser.add_argument("day_counts", nargs="+", type=int, help="Williams %%R day counts to calculate")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(return_williams_r(args.symbol, args.day_counts, args.as_of_date))


if __name__ == "__main__":
    main()
