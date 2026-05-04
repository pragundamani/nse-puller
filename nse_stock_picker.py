#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import requests

from nse_puller import DEFAULT_HEADERS, DEFAULT_SYMBOLS_FILE, PROJECT_DIR, split_symbols


SYMBOL_LIST_URLS = (
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
)
SYMBOL_LIST_CACHE = PROJECT_DIR / ".cache" / "symbols" / "EQUITY_L.csv"
DEFAULT_PAGE_SIZE = 20
DEFAULT_STOCKS_HEADER = [
    "# Stocks selected by nse-stock-picker.",
    "# One symbol per line.",
]


@dataclass(frozen=True)
class StockRecord:
    symbol: str
    name: str
    series: str
    isin: str


def load_questionary_module():
    try:
        import questionary
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "The interactive stock picker requires 'questionary'. "
            "Run '. .venv/bin/activate' and use that shell, or run '.venv/bin/python nse_puller.py'."
        ) from exc

    return questionary


def fetch_symbol_records(refresh: bool, timeout: int, include_non_eq: bool) -> list[StockRecord]:
    csv_text = load_symbol_list_csv(refresh=refresh, timeout=timeout)
    reader = csv.DictReader(csv_text.splitlines())
    records: list[StockRecord] = []

    for row in reader:
        normalized = {key.strip(): value.strip() for key, value in row.items() if key is not None}
        series = normalized.get("SERIES", "")
        if not include_non_eq and series != "EQ":
            continue
        symbol = normalized.get("SYMBOL", "")
        if not symbol:
            continue
        records.append(
            StockRecord(
                symbol=symbol,
                name=normalized.get("NAME OF COMPANY", ""),
                series=series,
                isin=normalized.get("ISIN NUMBER", ""),
            )
        )

    records.sort(key=lambda record: record.symbol)
    return records


def load_symbol_list_csv(refresh: bool, timeout: int) -> str:
    if SYMBOL_LIST_CACHE.exists() and not refresh:
        return SYMBOL_LIST_CACHE.read_text(encoding="utf-8")

    last_error: Exception | None = None
    for url in SYMBOL_LIST_URLS:
        try:
            response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
            response.raise_for_status()
            SYMBOL_LIST_CACHE.parent.mkdir(parents=True, exist_ok=True)
            SYMBOL_LIST_CACHE.write_text(response.text, encoding="utf-8")
            return response.text
        except requests.RequestException as exc:
            last_error = exc

    if SYMBOL_LIST_CACHE.exists():
        return SYMBOL_LIST_CACHE.read_text(encoding="utf-8")

    raise RuntimeError("Could not download the NSE symbol list.") from last_error


def load_selected_symbols(stocks_file: Path) -> list[str]:
    if not stocks_file.exists():
        return []

    selected: list[str] = []
    seen: set[str] = set()
    for line in stocks_file.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        for symbol in split_symbols(line):
            cleaned = symbol.strip().upper()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                selected.append(cleaned)
    return selected


def load_header_lines(stocks_file: Path) -> list[str]:
    if not stocks_file.exists():
        return DEFAULT_STOCKS_HEADER

    header_lines: list[str] = []
    for line in stocks_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            header_lines.append(line)
            continue
        break

    if header_lines:
        return header_lines
    return DEFAULT_STOCKS_HEADER


def save_selected_symbols(stocks_file: Path, selected_symbols: list[str]) -> None:
    header_lines = load_header_lines(stocks_file)
    lines = [*header_lines, ""]
    lines.extend(selected_symbols)
    stocks_file.parent.mkdir(parents=True, exist_ok=True)
    stocks_file.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def pick_records(
    records: list[StockRecord],
    selected_symbols: set[str],
    query: str,
    page_size: int,
) -> list[StockRecord]:
    if query == "/selected":
        return [record for record in records if record.symbol in selected_symbols][:page_size]

    if not query:
        selected_first = [record for record in records if record.symbol in selected_symbols]
        remainder = [record for record in records if record.symbol not in selected_symbols]
        return [*selected_first, *remainder][:page_size]

    ranked = sorted(
        records,
        key=lambda record: (-score_record(record, query), record.symbol),
    )
    return [record for record in ranked if score_record(record, query) > 0][:page_size]


def score_record(record: StockRecord, query: str) -> int:
    query_text = query.strip().lower()
    if not query_text:
        return 1

    symbol = record.symbol.lower()
    name = record.name.lower()
    combined = f"{symbol} {name}"

    score = 0
    if query_text == symbol:
        score += 1000
    if symbol.startswith(query_text):
        score += 700
    if query_text in symbol:
        score += 500 - symbol.index(query_text)
    if query_text in name:
        score += 350 - name.index(query_text)
    if is_subsequence(query_text, symbol):
        score += 220
    if is_subsequence(query_text, combined):
        score += 120

    score += int(100 * SequenceMatcher(None, query_text, symbol).ratio())
    score += int(60 * SequenceMatcher(None, query_text, combined).ratio())
    return score


def is_subsequence(needle: str, haystack: str) -> bool:
    iterator = iter(haystack)
    return all(char in iterator for char in needle)


def format_choice(record: StockRecord) -> str:
    return f"{record.symbol:<15} {record.name} [{record.series}]"


def launch_picker(
    records: list[StockRecord],
    stocks_file: Path,
    page_size: int,
) -> int:
    questionary = load_questionary_module()
    selected_symbols = set(load_selected_symbols(stocks_file))

    print(f"Loaded {len(records)} NSE symbols")
    print(f"Current selection: {len(selected_symbols)} symbols from {stocks_file}")
    print("Type a search like 'rel', 'tata', or 'bank'.")
    print("Commands: /selected shows current picks, /save writes stocks.txt, /quit exits.")

    while True:
        query = questionary.text(
            "Search stocks",
            instruction="Enter filters, or /save, /selected, /quit",
        ).ask()

        if query is None or query.strip() == "/quit":
            print("No changes saved.")
            return 1

        query = query.strip()
        if query == "/save":
            save_selected_symbols(stocks_file, sorted(selected_symbols))
            print(f"Saved {len(selected_symbols)} symbols to {stocks_file}")
            return 0

        visible_records = pick_records(records, selected_symbols, query, page_size)
        if not visible_records:
            print("No matches for that query.")
            continue

        visible_symbols = {record.symbol for record in visible_records}
        choices = [
            questionary.Choice(
                title=format_choice(record),
                value=record.symbol,
                checked=record.symbol in selected_symbols,
            )
            for record in visible_records
        ]
        choices.extend(
            [
                questionary.Separator(),
                questionary.Choice(
                    title=f"Save and exit ({len(selected_symbols)} selected before this page)",
                    value="__save__",
                ),
                questionary.Choice(title="Exit without saving", value="__quit__"),
            ]
        )

        result = questionary.checkbox(
            f"Toggle selections for '{query or 'top results'}'",
            choices=choices,
            instruction="Space toggles, Enter applies",
            validate=lambda answer: True if answer else "Select items or choose a command.",
        ).ask()

        if result is None or "__quit__" in result:
            print("No changes saved.")
            return 1

        chosen_symbols = {value for value in result if not value.startswith("__")}
        selected_symbols.difference_update(visible_symbols)
        selected_symbols.update(chosen_symbols)
        print(f"Selected {len(selected_symbols)} symbols")

        if "__save__" in result:
            save_selected_symbols(stocks_file, sorted(selected_symbols))
            print(f"Saved {len(selected_symbols)} symbols to {stocks_file}")
            return 0


def run_picker_app(
    stocks_file: Path,
    page_size: int = DEFAULT_PAGE_SIZE,
    refresh_symbol_list: bool = False,
    include_non_eq: bool = False,
    timeout: int = 30,
) -> int:
    records = fetch_symbol_records(
        refresh=refresh_symbol_list,
        timeout=timeout,
        include_non_eq=include_non_eq,
    )
    return launch_picker(records, stocks_file, page_size)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Interactive fuzzy checklist picker for the NSE stocks file."
    )
    parser.add_argument(
        "--stocks-file",
        default=str(DEFAULT_SYMBOLS_FILE),
        help="Target stocks file to edit (default: project stocks.txt)",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help="How many fuzzy matches to show per page (default: 20)",
    )
    parser.add_argument(
        "--refresh-symbol-list",
        action="store_true",
        help="Refresh the cached NSE symbol universe before starting",
    )
    parser.add_argument(
        "--include-non-eq",
        action="store_true",
        help="Include non-EQ series in the picker",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP timeout in seconds (default: 30)",
    )
    return parser


def run() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.page_size < 1:
        parser.error("--page-size must be at least 1")

    try:
        return run_picker_app(
            stocks_file=Path(args.stocks_file),
            page_size=args.page_size,
            refresh_symbol_list=args.refresh_symbol_list,
            include_non_eq=args.include_non_eq,
            timeout=args.timeout,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"Failed to load NSE symbol list: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
