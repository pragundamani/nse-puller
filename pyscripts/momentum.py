#!/usr/bin/env python3

from __future__ import annotations

import argparse

try:
    from pyscripts.common import load_stock_data, validate_day_counts
except ModuleNotFoundError:
    from common import load_stock_data, validate_day_counts


def return_momentum(symbol: str, day_counts: list[int], as_of_date: str) -> dict[int, float]:
    validated_day_counts = validate_day_counts(day_counts)
    normalized_symbol, df, resolved_date = load_stock_data(symbol, as_of_date, ["close"])
    close_series = df["close"].reset_index(drop=True)

    momentum_values: dict[int, float] = {}
    for day_count in validated_day_counts:
        if len(close_series) <= day_count:
            raise ValueError(
                f"Not enough data to compute momentum({day_count}) for {normalized_symbol} "
                f"through {resolved_date.date().isoformat()}"
            )
        prior_close = float(close_series.iloc[-(day_count + 1)])
        current_close = float(close_series.iloc[-1])
        if prior_close == 0.0:
            raise ValueError(f"Cannot compute momentum({day_count}) with prior close equal to 0")
        momentum_values[day_count] = ((current_close / prior_close) - 1.0) * 100.0

    return momentum_values


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest momentum values for a stock symbol")
    parser.add_argument("symbol", help="Stock symbol from stocks.txt")
    parser.add_argument("day_counts", nargs="+", type=int, help="Momentum day counts to calculate")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(return_momentum(args.symbol, args.day_counts, args.as_of_date))


if __name__ == "__main__":
    main()
