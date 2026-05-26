#!/usr/bin/env python3

from __future__ import annotations

import argparse

import pandas as pd

try:
    from pyscripts.common import DEFAULT_STOCKS_DIR, normalize_symbol, resolve_as_of_date, validate_day_counts
except ModuleNotFoundError:
    from common import DEFAULT_STOCKS_DIR, normalize_symbol, resolve_as_of_date, validate_day_counts


def _safe_ratio(current: float, average: float) -> float:
    if average == 0.0:
        return 0.0
    return current / average


def return_liquidity(symbol: str, day_counts: list[int], as_of_date: str) -> dict[str, object]:
    validated_day_counts = validate_day_counts(day_counts)
    normalized_symbol = normalize_symbol(symbol)
    stock_file = DEFAULT_STOCKS_DIR / f"{normalized_symbol}.csv"
    if not stock_file.exists():
        raise FileNotFoundError(f"Stock data file not found: {stock_file}")

    df = pd.read_csv(stock_file, usecols=["date", "volume", "value", "trades"])
    if df.empty:
        raise ValueError(f"No stock data found in {stock_file}")

    df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d", errors="raise")
    df = df.sort_values("date").reset_index(drop=True)
    resolved_date = resolve_as_of_date(df["date"], as_of_date)
    df = df[df["date"] <= resolved_date].copy()

    for column in ["volume", "value"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
        if df[column].isna().any():
            raise ValueError(f"Invalid {column} values found in {stock_file}")

    # Older rows can have missing trade counts; keep liquidity usable by treating them as zero.
    df["trades"] = pd.to_numeric(df["trades"], errors="coerce").fillna(0.0)

    for column in ["volume", "value", "trades"]:
        if (df[column] < 0).any():
            raise ValueError(f"{column} cannot contain negative values")

    current_volume = float(df["volume"].iloc[-1])
    current_value = float(df["value"].iloc[-1])
    current_trades = float(df["trades"].iloc[-1])

    rolling: dict[int, dict[str, float]] = {}
    for day_count in validated_day_counts:
        if len(df) < day_count:
            raise ValueError(
                f"Not enough data to compute liquidity({day_count}) for {normalized_symbol} "
                f"through {resolved_date.date().isoformat()}"
            )

        avg_volume = float(df["volume"].rolling(window=day_count, min_periods=day_count).mean().iloc[-1])
        avg_value = float(df["value"].rolling(window=day_count, min_periods=day_count).mean().iloc[-1])
        avg_trades = float(df["trades"].rolling(window=day_count, min_periods=day_count).mean().iloc[-1])

        volume_ratio = _safe_ratio(current_volume, avg_volume)
        value_ratio = _safe_ratio(current_value, avg_value)
        trades_ratio = _safe_ratio(current_trades, avg_trades)
        liquidity_score = (0.5 * volume_ratio) + (0.3 * value_ratio) + (0.2 * trades_ratio)

        rolling[day_count] = {
            "avg_volume": avg_volume,
            "avg_value": avg_value,
            "avg_trades": avg_trades,
            "volume_ratio": volume_ratio,
            "value_ratio": value_ratio,
            "trades_ratio": trades_ratio,
            "liquidity_score": liquidity_score,
        }

    return {
        "raw": {
            "volume": current_volume,
            "value": current_value,
            "trades": current_trades,
        },
        "rolling": rolling,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest liquidity values for a stock symbol")
    parser.add_argument("symbol", help="Stock symbol from stocks.txt")
    parser.add_argument("day_counts", nargs="+", type=int, help="Liquidity day counts to calculate")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(return_liquidity(args.symbol, args.day_counts, args.as_of_date))


if __name__ == "__main__":
    main()
