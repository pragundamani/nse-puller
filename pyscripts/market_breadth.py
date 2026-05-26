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


def _date_from_market_file(filename: str) -> date:
    code = filename.removesuffix(".csv")
    if not code.startswith("MA") or len(code) != 8:
        raise ValueError(f"Unexpected market activity filename: {filename}")
    return date(2000 + int(code[6:8]), int(code[4:6]), int(code[2:4]))


def _load_market_breadth_history(market_activity_dir: Path = DEFAULT_MARKET_ACTIVITY_DIR) -> pd.DataFrame:
    rows: list[dict[str, int | date]] = []
    for file_path in sorted(market_activity_dir.rglob("*.csv")):
        parsed_date = _date_from_market_file(file_path.name)
        advances: int | None = None
        declines: int | None = None
        unchanged: int | None = None

        with file_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            for row in reader:
                if len(row) < 3:
                    continue
                label = row[1].strip().upper()
                value = row[2].strip().replace(",", "")
                if not value:
                    continue
                if label == "ADVANCES":
                    advances = int(float(value))
                elif label == "DECLINES":
                    declines = int(float(value))
                elif label == "UNCHANGED":
                    unchanged = int(float(value))

        if advances is not None and declines is not None and unchanged is not None:
            rows.append(
                {
                    "date": parsed_date,
                    "advances": advances,
                    "declines": declines,
                    "unchanged": unchanged,
                }
            )

    if not rows:
        raise ValueError(f"No market breadth rows found under {market_activity_dir}")

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d", errors="raise")
    return df.sort_values("date").reset_index(drop=True)


def _direction_from_net(net_advances: float) -> str:
    if net_advances > 0:
        return "positive"
    if net_advances < 0:
        return "negative"
    return "neutral"


def return_market_breadth(day_counts: list[int], as_of_date: str) -> dict[str, object]:
    validated_day_counts = validate_day_counts(day_counts)
    requested_date = pd.Timestamp(_parse_requested_date(as_of_date))
    df = _load_market_breadth_history()

    future_dates = df.loc[df["date"] >= requested_date]
    if future_dates.empty:
        raise ValueError(f"No market breadth data found on or after {as_of_date}")

    resolved_index = int(future_dates.index[0])
    resolved_date = df.loc[resolved_index, "date"]
    df = df.iloc[: resolved_index + 1].copy().reset_index(drop=True)

    advances = int(df["advances"].iloc[-1])
    declines = int(df["declines"].iloc[-1])
    unchanged = int(df["unchanged"].iloc[-1])
    net_advances = advances - declines
    advance_decline_ratio = 0.0 if declines == 0 else advances / declines

    rolling: dict[int, dict[str, float | str]] = {}
    for day_count in validated_day_counts:
        if len(df) < day_count:
            raise ValueError(
                f"Not enough market breadth history to compute rolling breadth({day_count}) "
                f"through {resolved_date.date().isoformat()}"
            )

        window = df.iloc[-day_count:]
        avg_advances = float(window["advances"].mean())
        avg_declines = float(window["declines"].mean())
        avg_unchanged = float(window["unchanged"].mean())
        avg_net_advances = avg_advances - avg_declines
        avg_ad_ratio = 0.0 if avg_declines == 0.0 else avg_advances / avg_declines
        rolling[day_count] = {
            "avg_advances": avg_advances,
            "avg_declines": avg_declines,
            "avg_unchanged": avg_unchanged,
            "avg_net_advances": avg_net_advances,
            "avg_advance_decline_ratio": avg_ad_ratio,
            "breadth_direction": _direction_from_net(avg_net_advances),
        }

    return {
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "advance_decline_ratio": advance_decline_ratio,
        "net_advances": net_advances,
        "breadth_direction": _direction_from_net(net_advances),
        "rolling": rolling,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Return latest market breadth values")
    parser.add_argument("day_counts", nargs="+", type=int, help="Rolling market breadth day counts to calculate")
    parser.add_argument("--date", required=True, dest="as_of_date", help="As-of date in YYYY-MM-DD format")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    print(return_market_breadth(args.day_counts, args.as_of_date))


if __name__ == "__main__":
    main()
