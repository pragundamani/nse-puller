#!/usr/bin/env python3

from __future__ import annotations

import argparse

try:
    from pyscripts.common import load_stock_data
except ModuleNotFoundError:
    from common import load_stock_data


def return_obv(symbol: str, as_of_date: str) -> float:
    _, df, _ = load_stock_data(symbol, as_of_date, ["close", "volume"])

    if (df["volume"] < 0).any():
        raise ValueError("volume cannot contain negative values")

    close_series = df["close"].reset_index(drop=True)
    volume_series = df["volume"].reset_index(drop=True)

    obv = float(volume_series.iloc[0])
    for index in range(1, len(df)):
        current_close = float(close_series.iloc[index])
        previous_close = float(close_series.iloc[index - 1])
        current_volume = float(volume_series.iloc[index])

        if current_close > previous_close:
            obv += current_volume
        elif current_close < previous_close:
            obv -= current_volume

    return obv


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest OBV value for a stock symbol")
    parser.add_argument("symbol", help="Stock symbol from stocks.txt")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(return_obv(args.symbol, args.as_of_date))


if __name__ == "__main__":
    main()
