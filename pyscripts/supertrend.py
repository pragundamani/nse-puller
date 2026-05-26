#!/usr/bin/env python3

from __future__ import annotations

import argparse

import pandas as pd

try:
    from pyscripts.common import calculate_wilder_atr_series, load_stock_data, validate_day_counts
except ModuleNotFoundError:
    from common import calculate_wilder_atr_series, load_stock_data, validate_day_counts


def _validate_multipliers(multipliers: list[float]) -> list[float]:
    if not multipliers:
        raise ValueError("multipliers cannot be empty")
    if any(multiplier <= 0 for multiplier in multipliers):
        raise ValueError("multipliers must contain only positive values")
    if len(set(multipliers)) != len(multipliers):
        raise ValueError("multipliers cannot contain duplicates")
    return multipliers

def _calculate_supertrend(df: pd.DataFrame, day_count: int, multiplier: float) -> dict[str, float | str]:
    if len(df) <= day_count:
        raise ValueError(f"Not enough data to compute Supertrend({day_count}, {multiplier})")

    atr = calculate_wilder_atr_series(df, day_count)
    hl2 = (df["high"] + df["low"]) / 2.0
    basic_upper = hl2 + (multiplier * atr)
    basic_lower = hl2 - (multiplier * atr)

    final_upper = pd.Series(index=df.index, dtype=float)
    final_lower = pd.Series(index=df.index, dtype=float)
    supertrend = pd.Series(index=df.index, dtype=float)

    start_index = atr.first_valid_index()
    if start_index is None:
        raise ValueError(f"Not enough data to compute Supertrend({day_count}, {multiplier})")

    final_upper.iloc[start_index] = float(basic_upper.iloc[start_index])
    final_lower.iloc[start_index] = float(basic_lower.iloc[start_index])
    supertrend.iloc[start_index] = float(final_upper.iloc[start_index])

    for index in range(start_index + 1, len(df)):
        previous_index = index - 1
        previous_close = float(df["close"].iloc[previous_index])

        current_basic_upper = float(basic_upper.iloc[index])
        current_basic_lower = float(basic_lower.iloc[index])
        previous_final_upper = float(final_upper.iloc[previous_index])
        previous_final_lower = float(final_lower.iloc[previous_index])
        previous_supertrend = float(supertrend.iloc[previous_index])
        current_close = float(df["close"].iloc[index])

        if current_basic_upper < previous_final_upper or previous_close > previous_final_upper:
            final_upper.iloc[index] = current_basic_upper
        else:
            final_upper.iloc[index] = previous_final_upper

        if current_basic_lower > previous_final_lower or previous_close < previous_final_lower:
            final_lower.iloc[index] = current_basic_lower
        else:
            final_lower.iloc[index] = previous_final_lower

        if previous_supertrend == previous_final_upper:
            if current_close <= float(final_upper.iloc[index]):
                supertrend.iloc[index] = float(final_upper.iloc[index])
            else:
                supertrend.iloc[index] = float(final_lower.iloc[index])
        else:
            if current_close >= float(final_lower.iloc[index]):
                supertrend.iloc[index] = float(final_lower.iloc[index])
            else:
                supertrend.iloc[index] = float(final_upper.iloc[index])

    last_index = supertrend.last_valid_index()
    if last_index is None:
        raise ValueError(f"Not enough data to compute Supertrend({day_count}, {multiplier})")

    final_supertrend = float(supertrend.iloc[last_index])
    final_upper_band = float(final_upper.iloc[last_index])
    final_lower_band = float(final_lower.iloc[last_index])
    direction = "down" if final_supertrend == final_upper_band else "up"

    return {
        "supertrend": final_supertrend,
        "upper_band": final_upper_band,
        "lower_band": final_lower_band,
        "direction": direction,
    }


def return_supertrend(
    symbol: str,
    day_counts: list[int],
    multipliers: list[float],
    as_of_date: str,
) -> dict[tuple[int, float], dict[str, float | str]]:
    validated_day_counts = validate_day_counts(day_counts)
    validated_multipliers = _validate_multipliers(multipliers)
    normalized_symbol, df, resolved_date = load_stock_data(symbol, as_of_date, ["high", "low", "close"])

    supertrend_values: dict[tuple[int, float], dict[str, float | str]] = {}
    usable_rows = len(df)
    for day_count in validated_day_counts:
        if usable_rows <= day_count:
            raise ValueError(
                f"Not enough data to compute Supertrend({day_count}) for {normalized_symbol} "
                f"through {resolved_date.date().isoformat()}"
            )
        for multiplier in validated_multipliers:
            supertrend_values[(day_count, multiplier)] = _calculate_supertrend(df, day_count, multiplier)

    return supertrend_values


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest Supertrend values for a stock symbol")
    parser.add_argument("symbol", help="Stock symbol from stocks.txt")
    parser.add_argument("day_counts", nargs="+", type=int, help="Supertrend day counts to calculate")
    parser.add_argument("--multipliers", nargs="+", type=float, required=True, help="Supertrend multipliers to calculate")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(return_supertrend(args.symbol, args.day_counts, args.multipliers, args.as_of_date))


if __name__ == "__main__":
    main()
