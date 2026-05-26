#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path

import pandas as pd

try:
    from pyscripts.common import validate_day_counts
except ModuleNotFoundError:
    from common import validate_day_counts


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_MARKET_ACTIVITY_DIR = PROJECT_DIR / "out" / "market-activity"


def _parse_requested_date(as_of_date: str) -> date:
    try:
        return date.fromisoformat(as_of_date)
    except ValueError as exc:
        raise ValueError("as_of_date must be in YYYY-MM-DD format") from exc


def _clean_number(value: str) -> float:
    return float(value.replace(",", "").strip())


def _extract_nifty_row(file_path: Path) -> dict[str, float | str] | None:
    with file_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) < 7:
                continue
            index_name = row[1].strip()
            if index_name != "Nifty 50":
                continue
            return {
                "date": date.fromisoformat(file_path.stem[-2:] and _date_from_market_file(file_path.name).isoformat()),
                "previous_close": _clean_number(row[2]),
                "open": _clean_number(row[3]),
                "high": _clean_number(row[4]),
                "low": _clean_number(row[5]),
                "close": _clean_number(row[6]),
            }
    return None


def _date_from_market_file(filename: str) -> date:
    code = filename.removesuffix(".csv")
    if not code.startswith("MA") or len(code) != 8:
        raise ValueError(f"Unexpected market activity filename: {filename}")
    day = int(code[2:4])
    month = int(code[4:6])
    year = 2000 + int(code[6:8])
    return date(year, month, day)


def _load_nifty_history(market_activity_dir: Path = DEFAULT_MARKET_ACTIVITY_DIR) -> pd.DataFrame:
    rows: list[dict[str, float | str]] = []
    for file_path in sorted(market_activity_dir.rglob("*.csv")):
        parsed_date = _date_from_market_file(file_path.name)
        row = None
        with file_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            for raw_row in reader:
                if len(raw_row) < 7:
                    continue
                if raw_row[1].strip() != "Nifty 50":
                    continue
                row = {
                    "date": parsed_date,
                    "previous_close": _clean_number(raw_row[2]),
                    "open": _clean_number(raw_row[3]),
                    "high": _clean_number(raw_row[4]),
                    "low": _clean_number(raw_row[5]),
                    "close": _clean_number(raw_row[6]),
                }
                break
        if row is not None:
            rows.append(row)

    if not rows:
        raise ValueError(f"No Nifty 50 rows found under {market_activity_dir}")

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d", errors="raise")
    return df.sort_values("date").reset_index(drop=True)


def return_nifty_trend(day_counts: list[int], as_of_date: str) -> dict[str, float | dict[int, dict[str, float | str | bool]]]:
    validated_day_counts = validate_day_counts(day_counts)
    requested_date = pd.Timestamp(_parse_requested_date(as_of_date))
    df = _load_nifty_history()

    future_dates = df.loc[df["date"] >= requested_date]
    if future_dates.empty:
        raise ValueError(f"No Nifty market activity found on or after {as_of_date}")
    resolved_index = int(future_dates.index[0])
    resolved_date = df.loc[resolved_index, "date"]
    df = df.iloc[: resolved_index + 1].copy()

    close_series = df["close"].reset_index(drop=True)
    current_close = float(close_series.iloc[-1])
    trend_values: dict[int, dict[str, float | str | bool]] = {}
    for day_count in validated_day_counts:
        if len(close_series) <= day_count:
            raise ValueError(
                f"Not enough Nifty history to compute trend({day_count}) through {resolved_date.date().isoformat()}"
            )

        prior_close = float(close_series.iloc[-(day_count + 1)])
        return_pct = ((current_close / prior_close) - 1.0) * 100.0
        direction = "up" if return_pct > 0 else "down" if return_pct < 0 else "flat"

        ema_series = close_series.ewm(span=day_count, adjust=False, min_periods=day_count).mean()
        current_ema = float(ema_series.iloc[-1])
        previous_ema = float(ema_series.iloc[-2])
        ema_direction = "up" if current_ema > previous_ema else "down" if current_ema < previous_ema else "flat"

        trend_values[day_count] = {
            "return_pct": return_pct,
            "direction": direction,
            "ema": current_ema,
            "above_ema": current_close > current_ema,
            "ema_direction": ema_direction,
        }

    return {
        "close": current_close,
        "trends": trend_values,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest Nifty trend values")
    parser.add_argument("day_counts", nargs="+", type=int, help="Nifty trend day counts to calculate")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(return_nifty_trend(args.day_counts, args.as_of_date))


if __name__ == "__main__":
    main()
