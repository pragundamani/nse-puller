#!/usr/bin/env python3

from __future__ import annotations

import argparse

try:
    from pyscripts.common import calculate_wilder_atr_series, load_stock_data, validate_day_counts
except ModuleNotFoundError:
    from common import calculate_wilder_atr_series, load_stock_data, validate_day_counts


def return_keltner_channel(
    symbol: str,
    day_counts: list[int],
    as_of_date: str,
    multiplier: float = 2.0,
) -> dict[int, dict[str, float]]:
    if multiplier <= 0:
        raise ValueError("multiplier must be greater than 0")

    validated_day_counts = validate_day_counts(day_counts)
    normalized_symbol, df, resolved_date = load_stock_data(symbol, as_of_date, ["high", "low", "close"])
    close_series = df["close"]

    keltner_values: dict[int, dict[str, float]] = {}
    usable_rows = len(df)
    for day_count in validated_day_counts:
        if usable_rows <= day_count:
            raise ValueError(
                f"Not enough data to compute Keltner Channel({day_count}) for {normalized_symbol} "
                f"through {resolved_date.date().isoformat()}"
            )

        middle_series = close_series.ewm(span=day_count, adjust=False, min_periods=day_count).mean()
        atr_series = calculate_wilder_atr_series(df, day_count)
        last_index = middle_series.last_valid_index()
        if last_index is None or last_index not in atr_series.index or atr_series.loc[last_index] != atr_series.loc[last_index]:
            raise ValueError(f"Not enough data to compute Keltner Channel({day_count})")

        middle = float(middle_series.loc[last_index])
        atr_value = float(atr_series.loc[last_index])
        upper = middle + (multiplier * atr_value)
        lower = middle - (multiplier * atr_value)
        bandwidth = 0.0 if middle == 0.0 else ((upper - lower) / middle) * 100.0

        keltner_values[day_count] = {
            "upper": upper,
            "middle": middle,
            "lower": lower,
            "bandwidth": bandwidth,
        }

    return keltner_values


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest Keltner Channel values for a stock symbol")
    parser.add_argument("symbol", help="Stock symbol from stocks.txt")
    parser.add_argument("day_counts", nargs="+", type=int, help="Keltner Channel day counts to calculate")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    parser.add_argument("--multiplier", type=float, default=2.0, help="ATR multiplier")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(return_keltner_channel(args.symbol, args.day_counts, args.as_of_date, multiplier=args.multiplier))


if __name__ == "__main__":
    main()
