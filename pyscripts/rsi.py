#!/usr/bin/env python3

from __future__ import annotations

import argparse

try:
    from pyscripts.common import load_stock_data, validate_day_counts
except ModuleNotFoundError:
    from common import load_stock_data, validate_day_counts


def _calculate_wilder_rsi(close_series: pd.Series, day_count: int) -> float:
    if len(close_series) <= day_count:
        raise ValueError(f"Not enough data to compute RSI({day_count})")

    deltas = close_series.diff().dropna()
    gains = deltas.clip(lower=0)
    losses = -deltas.clip(upper=0)

    average_gain = float(gains.iloc[:day_count].mean())
    average_loss = float(losses.iloc[:day_count].mean())

    for index in range(day_count, len(deltas)):
        average_gain = ((average_gain * (day_count - 1)) + float(gains.iloc[index])) / day_count
        average_loss = ((average_loss * (day_count - 1)) + float(losses.iloc[index])) / day_count

    if average_loss == 0.0:
        if average_gain == 0.0:
            return 50.0
        return 100.0

    rs = average_gain / average_loss
    return 100.0 - (100.0 / (1.0 + rs))


def return_rsi(symbol: str, day_counts: list[int], as_of_date: str) -> dict[int, float]:
    validated_day_counts = validate_day_counts(day_counts)
    normalized_symbol, df, resolved_date = load_stock_data(symbol, as_of_date, ["close"])

    close_series = df["close"]

    rsi_values: dict[int, float] = {}
    usable_rows = len(close_series)
    for day_count in validated_day_counts:
        if usable_rows <= day_count:
            raise ValueError(
                f"Not enough data to compute RSI({day_count}) for {normalized_symbol} "
                f"through {resolved_date.date().isoformat()}"
            )
        rsi_values[day_count] = _calculate_wilder_rsi(close_series, day_count)

    return rsi_values


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest Wilder RSI values for a stock symbol")
    parser.add_argument("symbol", help="Stock symbol from stocks.txt")
    parser.add_argument("day_counts", nargs="+", type=int, help="RSI day counts to calculate")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(return_rsi(args.symbol, args.day_counts, args.as_of_date))


if __name__ == "__main__":
    main()
