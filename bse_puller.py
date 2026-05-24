#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from nse_puller import (
    DEFAULT_HEADERS,
    DEFAULT_SYMBOLS_FILE,
    PROJECT_DIR,
    RunLogger,
    append_symbol_rows,
    iter_weekdays,
    load_symbols,
    parse_date,
    read_last_saved_date,
    write_output,
    write_progress,
)


DEFAULT_OUT_DIR = PROJECT_DIR / "out"
DEFAULT_BSE_MAPPINGS_FILE = PROJECT_DIR / "bse-stocks.txt"
BSE_CACHE_DIR = PROJECT_DIR / ".cache" / "bse-bhavcopy"
BSE_ARCHIVE_BASE_URL = "https://www.bseindia.com/download/BhavCopy/Equity"
DEFAULT_START_DATE = date(2016, 12, 8)


class BSEArchiveClient:
    def __init__(self, timeout: int = 30, cache_dir: Path | None = None) -> None:
        self.timeout = timeout
        self.cache_dir = cache_dir or BSE_CACHE_DIR

    def fetch_history(
        self,
        symbol_map: dict[str, str],
        start_date: date,
        end_date: date,
    ) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for day in iter_weekdays(start_date, end_date):
            rows.extend(self.fetch_day(day, symbol_map))
        rows.sort(key=lambda row: (row["symbol"], row["date"]))
        return rows

    def fetch_day(self, day: date, symbol_map: dict[str, str]) -> list[dict[str, str]]:
        archive_bytes = self.get_archive(day)
        if archive_bytes is None:
            return []

        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            names = archive.namelist()
            if not names:
                return []
            text = archive.read(names[0]).decode("utf-8", errors="replace")

        reader = csv.DictReader(io.StringIO(text))
        matched_rows: list[dict[str, str]] = []
        for raw_row in reader:
            code = (raw_row.get("SC_CODE") or "").strip()
            shared_symbol = symbol_map.get(code)
            if shared_symbol is None:
                continue
            matched_rows.append(normalize_bse_row(shared_symbol, raw_row, day))
        return matched_rows

    def get_archive(self, day: date) -> bytes | None:
        cache_path = self.cache_path(day)
        if cache_path.exists():
            cached_bytes = cache_path.read_bytes()
            if is_zip_payload(cached_bytes):
                return cached_bytes
            cache_path.unlink()
            return None

        response = self.request_url(build_archive_url(day))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        if not is_zip_payload(response.content):
            return None

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(response.content)
        return response.content

    def request_url(self, url: str) -> requests.Response:
        last_response: requests.Response | None = None

        for attempt in range(4):
            response = requests.get(url, headers=DEFAULT_HEADERS, timeout=self.timeout)
            last_response = response

            if response.status_code not in {403, 429, 500, 502, 503, 504}:
                return response

            if attempt < 3:
                time.sleep(1.5 * (attempt + 1))

        assert last_response is not None
        return last_response

    def cache_path(self, day: date) -> Path:
        month = day.strftime("%b").upper()
        filename = f"EQ_ISINCODE_{day.strftime('%d%m%y')}.zip"
        return self.cache_dir / f"{day:%Y}" / month / filename


def build_archive_url(day: date) -> str:
    filename = f"EQ_ISINCODE_{day.strftime('%d%m%y')}.zip"
    return f"{BSE_ARCHIVE_BASE_URL}/{filename}"


def is_zip_payload(payload: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            archive.namelist()
        return True
    except zipfile.BadZipFile:
        return False


def normalize_bse_row(shared_symbol: str, raw_row: dict[str, str], trading_day: date) -> dict[str, str]:
    trading_date = normalize_bse_date(raw_row.get("TRADING_DATE", "")) or trading_day.isoformat()
    return {
        "symbol": shared_symbol,
        "series": (raw_row.get("SC_TYPE") or "").strip(),
        "date": trading_date,
        "open": (raw_row.get("OPEN") or "").strip(),
        "high": (raw_row.get("HIGH") or "").strip(),
        "low": (raw_row.get("LOW") or "").strip(),
        "close": (raw_row.get("CLOSE") or "").strip(),
        "last": (raw_row.get("LAST") or "").strip(),
        "previous_close": (raw_row.get("PREVCLOSE") or "").strip(),
        "volume": (raw_row.get("NO_OF_SHRS") or "").strip(),
        "value": (raw_row.get("NET_TURNOV") or "").strip(),
        "trades": (raw_row.get("NO_TRADES") or "").strip(),
        "isin": (raw_row.get("ISIN_CODE") or "").strip(),
    }


def normalize_bse_date(value: str) -> str:
    value = value.strip()
    for fmt in ("%d-%b-%y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return value


def load_bse_mappings(path: Path) -> dict[str, str]:
    mappings: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        parts = [part.strip() for part in stripped.split(",", 1)]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise RuntimeError(
                f"Invalid BSE mapping at {path}:{line_number}. Expected SHARED_SYMBOL,BSE_SCRIP_CODE"
            )
        mappings[parts[0].upper()] = parts[1]
    return mappings


def resolve_bse_codes(symbols: list[str], mappings_file: Path) -> dict[str, str]:
    mappings = load_bse_mappings(mappings_file)
    missing = [symbol for symbol in symbols if symbol not in mappings]
    if missing:
        missing_text = ", ".join(missing)
        raise RuntimeError(
            f"Missing BSE mappings for: {missing_text}. Update {mappings_file}."
        )
    return {symbol: mappings[symbol] for symbol in symbols}


def invert_bse_codes(symbol_to_code: dict[str, str]) -> dict[str, str]:
    code_to_symbol: dict[str, str] = {}
    duplicates: list[str] = []
    for symbol, code in symbol_to_code.items():
        if code in code_to_symbol:
            duplicates.append(code)
            continue
        code_to_symbol[code] = symbol
    if duplicates:
        duplicate_text = ", ".join(sorted(set(duplicates)))
        raise RuntimeError(f"Duplicate BSE scrip codes in mappings: {duplicate_text}")
    return code_to_symbol


def symbol_output_path(out_dir: Path, symbol: str) -> Path:
    return out_dir / "bse-stocks" / f"{symbol}.csv"


def update_symbol_files(
    client: BSEArchiveClient,
    symbol_to_code: dict[str, str],
    start_date: date,
    end_date: date,
    out_dir: Path,
    resume: bool,
    log_every: int,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    logger = RunLogger(out_dir)
    progress_path = out_dir / "bse-progress.json"
    (out_dir / "bse-stocks").mkdir(parents=True, exist_ok=True)

    symbol_start_dates: dict[str, date] = {}
    for symbol in symbol_to_code:
        effective_start = start_date
        last_saved = read_last_saved_date(symbol_output_path(out_dir, symbol)) if resume else None

        if last_saved is not None:
            candidate_start = last_saved + timedelta(days=1)
            if candidate_start > effective_start:
                effective_start = candidate_start
            logger.info(
                f"{symbol}: existing output found, resuming after {last_saved.isoformat()}"
            )

        if effective_start <= end_date:
            symbol_start_dates[symbol] = effective_start
        else:
            logger.info(f"{symbol}: already up to date")

    if not symbol_start_dates:
        payload = {
            "status": "completed",
            "started_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "requested_start_date": start_date.isoformat(),
            "requested_end_date": end_date.isoformat(),
            "symbols": sorted(symbol_to_code),
            "rows_written": 0,
            "days_processed": 0,
            "days_total": 0,
            "message": "All symbols were already up to date.",
        }
        write_progress(progress_path, payload)
        logger.info("All symbols are already up to date")
        return 0

    processing_days = [
        day
        for day in iter_weekdays(min(symbol_start_dates.values()), end_date)
        if any(symbol_start_dates[symbol] <= day for symbol in symbol_start_dates)
    ]

    progress = {
        "status": "running",
        "started_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "requested_start_date": start_date.isoformat(),
        "requested_end_date": end_date.isoformat(),
        "symbols": sorted(symbol_to_code),
        "active_symbols": sorted(symbol_start_dates),
        "symbol_start_dates": {
            symbol: symbol_start_dates[symbol].isoformat() for symbol in sorted(symbol_start_dates)
        },
        "days_processed": 0,
        "days_total": len(processing_days),
        "last_processed_day": None,
        "rows_written": 0,
        "symbol_rows_written": {symbol: 0 for symbol in sorted(symbol_start_dates)},
    }
    write_progress(progress_path, progress)
    logger.info(f"Starting BSE update for {len(symbol_start_dates)} symbols into {out_dir}")

    rows_written = 0
    for index, day in enumerate(processing_days, start=1):
        active_symbol_to_code = {
            symbol: code
            for symbol, code in symbol_to_code.items()
            if symbol in symbol_start_dates and symbol_start_dates[symbol] <= day
        }
        day_rows = client.fetch_day(day, invert_bse_codes(active_symbol_to_code))

        rows_by_symbol: dict[str, list[dict[str, str]]] = {}
        for row in day_rows:
            rows_by_symbol.setdefault(row["symbol"], []).append(row)

        for symbol, rows in rows_by_symbol.items():
            append_symbol_rows(symbol_output_path(out_dir, symbol), rows)
            progress["symbol_rows_written"][symbol] += len(rows)
            rows_written += len(rows)

        progress["updated_at"] = datetime.now().isoformat()
        progress["days_processed"] = index
        progress["last_processed_day"] = day.isoformat()
        progress["rows_written"] = rows_written
        write_progress(progress_path, progress)

        if index == 1 or index == len(processing_days) or index % log_every == 0:
            logger.info(
                f"Processed {index}/{len(processing_days)} trading days through {day.isoformat()}"
            )

    progress["status"] = "completed"
    progress["updated_at"] = datetime.now().isoformat()
    write_progress(progress_path, progress)
    logger.info(f"Finished BSE update with {rows_written} rows written")
    return rows_written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pull daily BSE historical equity data for the shared stock list."
    )
    parser.add_argument(
        "symbols",
        nargs="*",
        help="Shared symbols like ABB LT SBIN or a comma-separated list",
    )
    parser.add_argument(
        "--symbols-file",
        help="Text file with shared symbols, one per line or comma-separated. Defaults to stocks.txt in the project directory.",
    )
    parser.add_argument(
        "--mappings-file",
        default=str(DEFAULT_BSE_MAPPINGS_FILE),
        help="Shared-symbol to BSE scrip-code mappings file (default: ./bse-stocks.txt)",
    )
    parser.add_argument(
        "--start-date",
        type=parse_date,
        default=DEFAULT_START_DATE,
        help="Start date in YYYY-MM-DD format (default: 2016-12-08)",
    )
    parser.add_argument(
        "--end-date",
        type=parse_date,
        default=date.today(),
        help="End date in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json", "csv"),
        default="csv",
        help="Output format (default: csv)",
    )
    parser.add_argument(
        "--output",
        help="Optional combined output file path. Without this, CSV runs update one file per symbol under out/bse-stocks/.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT_DIR),
        help="Project-local output directory for per-symbol files, logs, and progress (default: ./out)",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Resume per-symbol CSV updates from the last saved date (default: enabled)",
    )
    parser.add_argument(
        "--log-every",
        type=int,
        default=50,
        help="Progress log interval in trading days for per-symbol updates (default: 50)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds (default: 30)",
    )
    return parser


def run() -> int:
    parser = build_parser()
    args = parser.parse_args()

    symbols_file = args.symbols_file
    if not args.symbols and symbols_file is None and DEFAULT_SYMBOLS_FILE.exists():
        symbols_file = str(DEFAULT_SYMBOLS_FILE)

    try:
        symbols = load_symbols(args.symbols, symbols_file)
    except OSError as exc:
        print(f"Could not read symbols file: {exc}", file=sys.stderr)
        return 1

    if not symbols:
        parser.error("provide at least one symbol or use --symbols-file")

    if args.start_date > args.end_date:
        parser.error("--start-date must be on or before --end-date")

    if args.log_every < 1:
        parser.error("--log-every must be at least 1")

    mappings_file = Path(args.mappings_file)
    try:
        symbol_to_code = resolve_bse_codes(symbols, mappings_file)
    except (OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    client = BSEArchiveClient(timeout=args.timeout)

    if args.output is None and args.format == "csv":
        try:
            update_symbol_files(
                client=client,
                symbol_to_code=symbol_to_code,
                start_date=args.start_date,
                end_date=args.end_date,
                out_dir=Path(args.out_dir),
                resume=args.resume,
                log_every=args.log_every,
            )
        except (requests.RequestException, RuntimeError) as exc:
            print(f"Failed to download BSE archive data: {exc}", file=sys.stderr)
            return 1
        return 0

    try:
        rows = client.fetch_history(
            symbol_map=invert_bse_codes(symbol_to_code),
            start_date=args.start_date,
            end_date=args.end_date,
        )
    except (requests.RequestException, RuntimeError) as exc:
        print(f"Failed to download BSE archive data: {exc}", file=sys.stderr)
        return 1

    if not rows:
        print(
            "No rows found for the requested symbols and date range.",
            file=sys.stderr,
        )
        return 1

    write_output(rows, args.format, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
