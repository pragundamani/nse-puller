#!/usr/bin/env python3

from __future__ import annotations

import argparse

try:
    from pyscripts.common import load_stock_data, validate_day_counts
except ModuleNotFoundError:
    from common import load_stock_data, validate_day_counts


def return_vwap(symbol: str, day_counts: list[int], as_of_date: str) -> dict[int, float]:
    validated_day_counts = validate_day_counts(day_counts)
    normalized_symbol, df, resolved_date = load_stock_data(
        symbol,
        as_of_date,
        ["open", "high", "low", "close", "volume"],
    )

    if (df["volume"] < 0).any():
        raise ValueError("volume cannot contain negative values")

    price_basis = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
    price_volume = price_basis * df["volume"]

    vwap_values: dict[int, float] = {}
    usable_rows = len(df)
    for day_count in validated_day_counts:
        if usable_rows < day_count:
            raise ValueError(
                f"Not enough data to compute VWAP({day_count}) for {normalized_symbol} "
                f"through {resolved_date.date().isoformat()}"
            )

        rolling_price_volume = price_volume.rolling(window=day_count, min_periods=day_count).sum()
        rolling_volume = df["volume"].rolling(window=day_count, min_periods=day_count).sum()

        final_price_volume = float(rolling_price_volume.iloc[-1])
        final_volume = float(rolling_volume.iloc[-1])
        if final_volume == 0.0:
            vwap_values[day_count] = 0.0
        else:
            vwap_values[day_count] = final_price_volume / final_volume

    return vwap_values


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest rolling VWAP values for a stock symbol")
    parser.add_argument("symbol", help="Stock symbol from stocks.txt")
    parser.add_argument("day_counts", nargs="+", type=int, help="VWAP day counts to calculate")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(return_vwap(args.symbol, args.day_counts, args.as_of_date))


if __name__ == "__main__":
    main()
