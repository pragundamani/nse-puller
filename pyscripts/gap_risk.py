#!/usr/bin/env python3

from __future__ import annotations

import argparse

try:
    from pyscripts.common import load_stock_data
except ModuleNotFoundError:
    from common import load_stock_data


def return_gap_risk(symbol: str, as_of_date: str) -> dict[str, float | str | bool]:
    _, df, _ = load_stock_data(symbol, as_of_date, ["open", "previous_close"])

    current_open = float(df["open"].iloc[-1])
    previous_close = float(df["previous_close"].iloc[-1])
    if previous_close == 0.0:
        raise ValueError("previous_close cannot be 0 when computing gap risk")

    gap_pct = ((current_open / previous_close) - 1.0) * 100.0
    if gap_pct > 0:
        gap_direction = "up"
    elif gap_pct < 0:
        gap_direction = "down"
    else:
        gap_direction = "flat"

    absolute_gap = abs(gap_pct)
    return {
        "previous_close": previous_close,
        "open": current_open,
        "gap_pct": gap_pct,
        "gap_direction": gap_direction,
        "risk_2pct": absolute_gap >= 2.0,
        "risk_3pct": absolute_gap >= 3.0,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest gap risk values for a stock symbol")
    parser.add_argument("symbol", help="Stock symbol from stocks.txt")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(return_gap_risk(args.symbol, args.as_of_date))


if __name__ == "__main__":
    main()
