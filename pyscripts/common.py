from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SYMBOLS_FILE = PROJECT_DIR / "stocks.txt"
DEFAULT_STOCKS_DIR = PROJECT_DIR / "out" / "stocks"


def load_symbols(symbols_file: Path = DEFAULT_SYMBOLS_FILE) -> set[str]:
    symbols: set[str] = set()
    for raw_line in symbols_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        symbols.add(line)
    return symbols


def normalize_symbol(symbol: str, symbols_file: Path = DEFAULT_SYMBOLS_FILE) -> str:
    normalized_symbol = symbol.strip().upper()
    if normalized_symbol not in load_symbols(symbols_file):
        raise ValueError(f"Symbol {normalized_symbol!r} not found in {symbols_file.name}")
    return normalized_symbol


def validate_day_counts(day_counts: list[int], label: str = "day_counts") -> list[int]:
    if not day_counts:
        raise ValueError(f"{label} cannot be empty")
    if any(day_count <= 0 for day_count in day_counts):
        raise ValueError(f"{label} must contain only positive integers")
    if len(set(day_counts)) != len(day_counts):
        raise ValueError(f"{label} cannot contain duplicates")
    return day_counts


def resolve_as_of_date(dates: pd.Series, as_of_date: str) -> pd.Timestamp:
    try:
        requested_date = pd.Timestamp(date.fromisoformat(as_of_date))
    except ValueError as exc:
        raise ValueError("as_of_date must be in YYYY-MM-DD format") from exc

    matching_dates = dates[dates >= requested_date]
    if matching_dates.empty:
        raise ValueError(f"No trading day found on or after {as_of_date}")
    return matching_dates.iloc[0]


def load_stock_data(
    symbol: str,
    as_of_date: str,
    columns: list[str],
    stocks_dir: Path = DEFAULT_STOCKS_DIR,
) -> tuple[str, pd.DataFrame, pd.Timestamp]:
    normalized_symbol = normalize_symbol(symbol)
    stock_file = stocks_dir / f"{normalized_symbol}.csv"
    if not stock_file.exists():
        raise FileNotFoundError(f"Stock data file not found: {stock_file}")

    required_columns = ["date", *columns]
    df = pd.read_csv(stock_file, usecols=required_columns)
    if df.empty:
        raise ValueError(f"No stock data found in {stock_file}")

    df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d", errors="raise")
    df = df.sort_values("date").reset_index(drop=True)
    resolved_date = resolve_as_of_date(df["date"], as_of_date)
    df = df[df["date"] <= resolved_date].copy()

    for column in columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")
        if df[column].isna().any():
            raise ValueError(f"Invalid {column} values found in {stock_file}")

    return normalized_symbol, df, resolved_date


def calculate_wilder_atr_series(df: pd.DataFrame, day_count: int) -> pd.Series:
    if len(df) <= day_count:
        raise ValueError(f"Not enough data to compute ATR({day_count})")

    previous_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    true_range = true_range.iloc[1:].reset_index(drop=True)
    atr = pd.Series(index=true_range.index, dtype=float)
    atr.iloc[day_count - 1] = float(true_range.iloc[:day_count].mean())

    for index in range(day_count, len(true_range)):
        atr.iloc[index] = ((float(atr.iloc[index - 1]) * (day_count - 1)) + float(true_range.iloc[index])) / day_count

    atr = pd.concat([pd.Series([float("nan")]), atr], ignore_index=True)
    atr.index = df.index
    return atr
