#!/usr/bin/env python3

from __future__ import annotations

import argparse

try:
    from pyscripts.common import load_stock_data
except ModuleNotFoundError:
    from common import load_stock_data


def return_parabolic_sar(
    symbol: str,
    as_of_date: str,
    acceleration_step: float = 0.02,
    max_acceleration: float = 0.20,
) -> dict[str, float | str]:
    if acceleration_step <= 0 or max_acceleration <= 0:
        raise ValueError("acceleration_step and max_acceleration must be positive")
    if acceleration_step > max_acceleration:
        raise ValueError("acceleration_step must be less than or equal to max_acceleration")

    _, df, _ = load_stock_data(symbol, as_of_date, ["high", "low", "close"])
    if len(df) < 2:
        raise ValueError("Not enough data to compute Parabolic SAR")

    first_close = float(df["close"].iloc[0])
    second_close = float(df["close"].iloc[1])
    direction = "up" if second_close >= first_close else "down"
    acceleration_factor = acceleration_step

    if direction == "up":
        sar = float(df["low"].iloc[0])
        extreme_point = max(float(df["high"].iloc[0]), float(df["high"].iloc[1]))
    else:
        sar = float(df["high"].iloc[0])
        extreme_point = min(float(df["low"].iloc[0]), float(df["low"].iloc[1]))

    for index in range(1, len(df)):
        tentative_sar = sar + acceleration_factor * (extreme_point - sar)

        if direction == "up":
            clamp_start = max(0, index - 2)
            tentative_sar = min(tentative_sar, float(df["low"].iloc[clamp_start:index].min()))
            current_low = float(df["low"].iloc[index])
            current_high = float(df["high"].iloc[index])
            if tentative_sar > current_low:
                direction = "down"
                sar = extreme_point
                extreme_point = current_low
                acceleration_factor = acceleration_step
            else:
                sar = tentative_sar
                if current_high > extreme_point:
                    extreme_point = current_high
                    acceleration_factor = min(acceleration_factor + acceleration_step, max_acceleration)
        else:
            clamp_start = max(0, index - 2)
            tentative_sar = max(tentative_sar, float(df["high"].iloc[clamp_start:index].max()))
            current_low = float(df["low"].iloc[index])
            current_high = float(df["high"].iloc[index])
            if tentative_sar < current_high:
                direction = "up"
                sar = extreme_point
                extreme_point = current_high
                acceleration_factor = acceleration_step
            else:
                sar = tentative_sar
                if current_low < extreme_point:
                    extreme_point = current_low
                    acceleration_factor = min(acceleration_factor + acceleration_step, max_acceleration)

    return {
        "sar": float(sar),
        "direction": direction,
        "extreme_point": float(extreme_point),
        "acceleration_factor": float(acceleration_factor),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest Parabolic SAR value for a stock symbol")
    parser.add_argument("symbol", help="Stock symbol from stocks.txt")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    parser.add_argument("--acceleration-step", type=float, default=0.02, help="Acceleration step")
    parser.add_argument("--max-acceleration", type=float, default=0.20, help="Maximum acceleration")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(
        return_parabolic_sar(
            args.symbol,
            args.as_of_date,
            acceleration_step=args.acceleration_step,
            max_acceleration=args.max_acceleration,
        )
    )


if __name__ == "__main__":
    main()
