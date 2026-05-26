#!/usr/bin/env python3

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd

try:
    from pyscripts.common import DEFAULT_STOCKS_DIR, normalize_symbol, validate_day_counts
except ModuleNotFoundError:
    from common import DEFAULT_STOCKS_DIR, normalize_symbol, validate_day_counts


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DELIVERY_DIR = PROJECT_DIR / "out" / "delivery-positions"


def _resolve_requested_date(as_of_date: str) -> date:
    try:
        return date.fromisoformat(as_of_date)
    except ValueError as exc:
        raise ValueError("as_of_date must be in YYYY-MM-DD format") from exc


def _available_delivery_dates(delivery_dir: Path) -> list[date]:
    dates: list[date] = []
    for path in delivery_dir.rglob("*.csv"):
        try:
            dates.append(date.fromisoformat(path.stem))
        except ValueError:
            continue
    return sorted(dates)


def _delivery_file_path(delivery_dir: Path, day: date) -> Path:
    return delivery_dir / f"{day:%Y}" / f"{day:%m}" / f"{day.isoformat()}.csv"


def _load_delivery_row(delivery_dir: Path, symbol: str, day: date) -> float:
    file_path = _delivery_file_path(delivery_dir, day)
    if not file_path.exists():
        raise FileNotFoundError(f"Delivery data file not found: {file_path}")

    df = pd.read_csv(file_path, usecols=["symbol", "delivery_pct"])
    row = df.loc[df["symbol"].astype(str).str.upper() == symbol, "delivery_pct"]
    if row.empty:
        raise ValueError(f"No delivery data found for {symbol} on {day.isoformat()}")
    return float(pd.to_numeric(row.iloc[0], errors="raise"))


def return_delivery_pct(symbol: str, day_counts: list[int], as_of_date: str) -> dict[str, float | dict[int, float]]:
    validated_day_counts = validate_day_counts(day_counts)
    normalized_symbol = normalize_symbol(symbol)
    if not (DEFAULT_STOCKS_DIR / f"{normalized_symbol}.csv").exists():
        raise FileNotFoundError(f"Stock data file not found: {DEFAULT_STOCKS_DIR / f'{normalized_symbol}.csv'}")

    requested_date = _resolve_requested_date(as_of_date)
    available_dates = _available_delivery_dates(DEFAULT_DELIVERY_DIR)
    resolved_date = next((day for day in available_dates if day >= requested_date), None)
    if resolved_date is None:
        raise ValueError(f"No delivery data found on or after {as_of_date}")

    resolved_index = available_dates.index(resolved_date)
    raw_delivery_pct = _load_delivery_row(DEFAULT_DELIVERY_DIR, normalized_symbol, resolved_date)

    rolling_values: dict[int, float] = {}
    for day_count in validated_day_counts:
        if resolved_index + 1 < day_count:
            raise ValueError(
                f"Not enough delivery history to compute rolling delivery %({day_count}) for {normalized_symbol} "
                f"through {resolved_date.isoformat()}"
            )

        window_dates = available_dates[resolved_index - day_count + 1 : resolved_index + 1]
        window_values = [_load_delivery_row(DEFAULT_DELIVERY_DIR, normalized_symbol, day) for day in window_dates]
        rolling_values[day_count] = float(sum(window_values) / day_count)

    return {
        "raw": raw_delivery_pct,
        "rolling": rolling_values,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest delivery percentage values for a stock symbol")
    parser.add_argument("symbol", help="Stock symbol from stocks.txt")
    parser.add_argument("day_counts", nargs="+", type=int, help="Rolling delivery percentage day counts to calculate")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(return_delivery_pct(args.symbol, args.day_counts, args.as_of_date))


if __name__ == "__main__":
    main()
