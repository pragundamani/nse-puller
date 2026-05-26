#!/usr/bin/env python3

from __future__ import annotations

import argparse

try:
    from pyscripts.common import load_stock_data, validate_day_counts
except ModuleNotFoundError:
    from common import load_stock_data, validate_day_counts


def return_volume_spike(
    symbol: str,
    day_counts: list[int],
    as_of_date: str,
    threshold: float = 2.0,
) -> dict[int, dict[str, float | bool]]:
    if threshold <= 0:
        raise ValueError("threshold must be greater than 0")

    validated_day_counts = validate_day_counts(day_counts)
    normalized_symbol, df, resolved_date = load_stock_data(symbol, as_of_date, ["volume"])

    if (df["volume"] < 0).any():
        raise ValueError("volume cannot contain negative values")

    current_volume = float(df["volume"].iloc[-1])
    volume_spike_values: dict[int, dict[str, float | bool]] = {}
    usable_rows = len(df)
    for day_count in validated_day_counts:
        if usable_rows < day_count:
            raise ValueError(
                f"Not enough data to compute volume spike({day_count}) for {normalized_symbol} "
                f"through {resolved_date.date().isoformat()}"
            )

        rolling_avg_volume = float(df["volume"].rolling(window=day_count, min_periods=day_count).mean().iloc[-1])
        if rolling_avg_volume == 0.0:
            ratio = 0.0
        else:
            ratio = current_volume / rolling_avg_volume

        volume_spike_values[day_count] = {
            "ratio": ratio,
            "is_spike": ratio >= threshold,
        }

    return volume_spike_values


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest volume spike values for a stock symbol")
    parser.add_argument("symbol", help="Stock symbol from stocks.txt")
    parser.add_argument("day_counts", nargs="+", type=int, help="Volume spike day counts to calculate")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    parser.add_argument("--threshold", type=float, default=2.0, help="Spike threshold ratio")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(return_volume_spike(args.symbol, args.day_counts, args.as_of_date, threshold=args.threshold))


if __name__ == "__main__":
    main()
