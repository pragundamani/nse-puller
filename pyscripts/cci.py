#!/usr/bin/env python3

from __future__ import annotations

import argparse

try:
    from pyscripts.common import load_stock_data, validate_day_counts
except ModuleNotFoundError:
    from common import load_stock_data, validate_day_counts


def return_cci(symbol: str, day_counts: list[int], as_of_date: str) -> dict[int, float]:
    validated_day_counts = validate_day_counts(day_counts)
    normalized_symbol, df, resolved_date = load_stock_data(symbol, as_of_date, ["high", "low", "close"])

    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    cci_values: dict[int, float] = {}
    usable_rows = len(df)

    for day_count in validated_day_counts:
        if usable_rows < day_count:
            raise ValueError(
                f"Not enough data to compute CCI({day_count}) for {normalized_symbol} "
                f"through {resolved_date.date().isoformat()}"
            )

        rolling_mean = typical_price.rolling(window=day_count, min_periods=day_count).mean()
        mean_deviation = typical_price.rolling(window=day_count, min_periods=day_count).apply(
            lambda values: float(abs(values - values.mean()).mean()),
            raw=False,
        )

        final_tp = float(typical_price.iloc[-1])
        final_mean = float(rolling_mean.iloc[-1])
        final_deviation = float(mean_deviation.iloc[-1])
        if final_deviation == 0.0:
            cci_values[day_count] = 0.0
        else:
            cci_values[day_count] = (final_tp - final_mean) / (0.015 * final_deviation)

    return cci_values


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest CCI values for a stock symbol")
    parser.add_argument("symbol", help="Stock symbol from stocks.txt")
    parser.add_argument("day_counts", nargs="+", type=int, help="CCI day counts to calculate")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(return_cci(args.symbol, args.day_counts, args.as_of_date))


if __name__ == "__main__":
    main()
