#!/usr/bin/env python3

from __future__ import annotations

import argparse

import pandas as pd

try:
    from pyscripts.common import load_stock_data
except ModuleNotFoundError:
    from common import load_stock_data


def _validate_configs(configs: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    if not configs:
        raise ValueError("configs cannot be empty")
    if len(set(configs)) != len(configs):
        raise ValueError("configs cannot contain duplicates")

    for fast_period, slow_period, signal_period in configs:
        if min(fast_period, slow_period, signal_period) <= 0:
            raise ValueError("MACD periods must be positive integers")
        if fast_period >= slow_period:
            raise ValueError("MACD fast period must be smaller than slow period")
    return configs


def _macd_series(close_series: pd.Series, config: tuple[int, int, int]) -> dict[str, float]:
    fast_period, slow_period, signal_period = config
    if len(close_series) < slow_period + signal_period - 1:
        raise ValueError(
            f"Not enough data to compute MACD{config}; need at least {slow_period + signal_period - 1} rows"
        )

    fast_ema = close_series.ewm(span=fast_period, adjust=False, min_periods=fast_period).mean()
    slow_ema = close_series.ewm(span=slow_period, adjust=False, min_periods=slow_period).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal_period, adjust=False, min_periods=signal_period).mean()
    histogram = macd_line - signal_line

    last_index = signal_line.last_valid_index()
    if last_index is None:
        raise ValueError(f"Not enough data to compute MACD{config}")

    return {
        "macd": float(macd_line.loc[last_index]),
        "signal": float(signal_line.loc[last_index]),
        "histogram": float(histogram.loc[last_index]),
    }


def return_macd(
    symbol: str,
    configs: list[tuple[int, int, int]],
    as_of_date: str,
) -> dict[tuple[int, int, int], dict[str, float]]:
    validated_configs = _validate_configs(configs)
    _, df, _ = load_stock_data(symbol, as_of_date, ["close"])
    close_series = df["close"]
    return {config: _macd_series(close_series, config) for config in validated_configs}


def _parse_config(value: str) -> tuple[int, int, int]:
    try:
        fast_period, slow_period, signal_period = (int(part) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("config must be FAST,SLOW,SIGNAL") from exc
    return fast_period, slow_period, signal_period


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest MACD values for a stock symbol")
    parser.add_argument("symbol", help="Stock symbol from stocks.txt")
    parser.add_argument("configs", nargs="+", type=_parse_config, help="MACD configs as FAST,SLOW,SIGNAL")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(return_macd(args.symbol, args.configs, args.as_of_date))


if __name__ == "__main__":
    main()
