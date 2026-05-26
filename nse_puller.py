#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import requests


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_SYMBOLS_FILE = PROJECT_DIR / "stocks.txt"
DEFAULT_OUT_DIR = PROJECT_DIR / "out"
PRIMARY_ARCHIVE_BASE_URL = "https://nsearchives.nseindia.com/content/historical/EQUITIES"
FALLBACK_ARCHIVE_BASE_URL = "https://archives.nseindia.com/content/historical/EQUITIES"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "application/zip,text/csv,text/plain,*/*",
}
DEFAULT_START_DATE = date(2008, 1, 1)
CACHE_DIR = PROJECT_DIR / ".cache" / "bhavcopy"
STATUS_CACHE_DIR = PROJECT_DIR / ".cache" / "bhavcopy-status"
OUTPUT_FIELDS = [
    "symbol",
    "series",
    "date",
    "open",
    "high",
    "low",
    "close",
    "last",
    "previous_close",
    "volume",
    "value",
    "trades",
    "isin",
]


class NSEArchiveClient:
    def __init__(
        self,
        timeout: int = 30,
        cache_dir: Path | None = None,
        requests_per_second: float = 3.0,
    ) -> None:
        self.timeout = timeout
        self.cache_dir = cache_dir or CACHE_DIR
        self.rate_limiter = RateLimiter(requests_per_second)

    def fetch_history(
        self,
        symbols: list[str],
        series: str,
        start_date: date,
        end_date: date,
        workers: int,
    ) -> list[dict]:
        symbol_set = {symbol.upper() for symbol in symbols}
        all_days = list(iter_weekdays(start_date, end_date))

        rows: list[dict] = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for day_rows in executor.map(
                lambda day: self.fetch_day(day, symbol_set, series),
                all_days,
            ):
                rows.extend(day_rows)

        rows.sort(key=lambda row: (row["symbol"], row["date"]))
        return rows

    def fetch_day(self, day: date, symbols: set[str], series: str) -> list[dict]:
        archive_bytes = self.get_archive(day)
        if archive_bytes is None:
            return []

        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            names = archive.namelist()
            if not names:
                return []

            text = archive.read(names[0]).decode("utf-8", errors="replace")

        reader = csv.DictReader(io.StringIO(text))
        matched_rows = []
        for raw_row in reader:
            if extract_series(raw_row) != series or extract_symbol(raw_row) not in symbols:
                continue
            matched_rows.append(normalize_history_row(raw_row))

        return matched_rows

    def get_archive(self, day: date) -> bytes | None:
        cache_path = self.cache_path(day)
        if cache_path.exists():
            return cache_path.read_bytes()

        cached_status = self.load_cached_status(day)
        if cached_status and cached_status.get("status") == "missing":
            return None

        for url_group in build_archive_url_groups(day):
            saw_missing = False
            last_response: requests.Response | None = None

            for url in url_group:
                response = self.request_url(url)
                last_response = response
                if response.status_code == 200:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_bytes(response.content)
                    self.write_cached_status(
                        day,
                        {
                            "status": "downloaded",
                            "url": url,
                            "cache_path": str(cache_path),
                            "bytes": len(response.content),
                            "checked_at": datetime.now().isoformat(),
                        },
                    )
                    return response.content
                if response.status_code == 404:
                    saw_missing = True
                    continue
                if saw_missing:
                    continue
                response.raise_for_status()

            if saw_missing:
                continue
            if last_response is not None:
                last_response.raise_for_status()

        self.write_cached_status(
            day,
            {
                "status": "missing",
                "checked_at": datetime.now().isoformat(),
                "urls": [url for url_group in build_archive_url_groups(day) for url in url_group],
            },
        )
        return None

    def request_url(self, url: str) -> requests.Response:
        last_response: requests.Response | None = None
        last_error: requests.RequestException | None = None

        for attempt in range(4):
            self.rate_limiter.wait()
            try:
                response = requests.get(url, headers=DEFAULT_HEADERS, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
            last_response = response

            if response.status_code not in {403, 429, 500, 502, 503, 504}:
                return response

            if attempt < 3:
                time.sleep(1.5 * (attempt + 1))

        if last_error is not None:
            raise last_error
        assert last_response is not None
        return last_response

    def cache_path(self, day: date) -> Path:
        month = day.strftime("%b").upper()
        filename = f"cm{day.strftime('%d%b%Y').upper()}bhav.csv.zip"
        return self.cache_dir / f"{day:%Y}" / month / filename

    def status_cache_path(self, day: date) -> Path:
        return STATUS_CACHE_DIR / f"{day.isoformat()}.json"

    def load_cached_status(self, day: date) -> dict | None:
        cache_path = self.status_cache_path(day)
        if not cache_path.exists():
            return None
        return json.loads(cache_path.read_text(encoding="utf-8"))

    def write_cached_status(self, day: date, payload: dict) -> None:
        cache_path = self.status_cache_path(day)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


class RateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        self.interval = 0.0 if requests_per_second <= 0 else 1.0 / requests_per_second
        self.lock = threading.Lock()
        self.next_request_time = 0.0

    def wait(self) -> None:
        if self.interval == 0.0:
            return

        with self.lock:
            now = time.monotonic()
            if now < self.next_request_time:
                time.sleep(self.next_request_time - now)
                now = time.monotonic()
            self.next_request_time = now + self.interval


class RunLogger:
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.logs_dir = out_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_log_path = self.logs_dir / f"run-{timestamp}.log"
        self.latest_log_path = self.logs_dir / "latest.log"
        self.latest_log_path.write_text("", encoding="utf-8")

    def info(self, message: str) -> None:
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}"
        print(line)
        with self.run_log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        with self.latest_log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def normalize_history_row(raw_row: dict[str, str]) -> dict:
    if "SYMBOL" in raw_row:
        return {
            "symbol": raw_row.get("SYMBOL", ""),
            "series": raw_row.get("SERIES", ""),
            "date": normalize_timestamp(raw_row.get("TIMESTAMP", "")),
            "open": raw_row.get("OPEN", ""),
            "high": raw_row.get("HIGH", ""),
            "low": raw_row.get("LOW", ""),
            "close": raw_row.get("CLOSE", ""),
            "last": raw_row.get("LAST", ""),
            "previous_close": raw_row.get("PREVCLOSE", ""),
            "volume": raw_row.get("TOTTRDQTY", ""),
            "value": raw_row.get("TOTTRDVAL", ""),
            "trades": raw_row.get("TOTALTRADES", ""),
            "isin": raw_row.get("ISIN", ""),
        }

    return {
        "symbol": raw_row.get("TckrSymb", ""),
        "series": raw_row.get("SctySrs", ""),
        "date": normalize_timestamp(raw_row.get("TradDt", "")),
        "open": raw_row.get("OpnPric", ""),
        "high": raw_row.get("HghPric", ""),
        "low": raw_row.get("LwPric", ""),
        "close": raw_row.get("ClsPric", ""),
        "last": raw_row.get("LastPric", ""),
        "previous_close": raw_row.get("PrvsClsgPric", ""),
        "volume": raw_row.get("TtlTradgVol", ""),
        "value": raw_row.get("TtlTrfVal", ""),
        "trades": raw_row.get("TtlNbOfTxsExctd", ""),
        "isin": raw_row.get("ISIN", ""),
    }


def extract_symbol(raw_row: dict[str, str]) -> str:
    return raw_row.get("SYMBOL") or raw_row.get("TckrSymb", "")


def extract_series(raw_row: dict[str, str]) -> str:
    return raw_row.get("SERIES") or raw_row.get("SctySrs", "")


def build_archive_url_groups(day: date) -> list[tuple[str, ...]]:
    month = day.strftime("%b").upper()
    old_filename = f"cm{day.strftime('%d%b%Y').upper()}bhav.csv.zip"
    new_filename = f"BhavCopy_NSE_CM_0_0_0_{day.strftime('%Y%m%d')}_F_0000.csv.zip"

    return [
        (
            f"{PRIMARY_ARCHIVE_BASE_URL}/{day:%Y}/{month}/{old_filename}",
            f"{FALLBACK_ARCHIVE_BASE_URL}/{day:%Y}/{month}/{old_filename}",
        ),
        (
            f"{PRIMARY_ARCHIVE_BASE_URL.rsplit('/content/historical/EQUITIES', 1)[0]}/content/cm/{new_filename}",
        ),
    ]


def normalize_timestamp(value: str) -> str:
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue

    try:
        return datetime.strptime(value.replace(" ", ""), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return value


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date '{value}', expected YYYY-MM-DD"
        ) from exc


def iter_weekdays(start_date: date, end_date: date):
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def load_symbols(direct_symbols: list[str], symbols_file: str | None) -> list[str]:
    raw_symbols: list[str] = []

    for item in direct_symbols:
        raw_symbols.extend(split_symbols(item))

    if symbols_file:
        text = Path(symbols_file).read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                raw_symbols.extend(split_symbols(line))

    symbols: list[str] = []
    seen: set[str] = set()
    for symbol in raw_symbols:
        cleaned = symbol.strip().upper()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            symbols.append(cleaned)

    return symbols


def split_symbols(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def render_table(rows: list[dict]) -> str:
    columns = [
        ("symbol", "SYMBOL"),
        ("date", "DATE"),
        ("open", "OPEN"),
        ("high", "HIGH"),
        ("low", "LOW"),
        ("close", "CLOSE"),
        ("volume", "VOLUME"),
    ]

    widths: dict[str, int] = {}
    for key, label in columns:
        widths[key] = len(label)
        for row in rows:
            widths[key] = max(widths[key], len(str(row.get(key, ""))))

    header = "  ".join(label.ljust(widths[key]) for key, label in columns)
    divider = "  ".join("-" * widths[key] for key, _ in columns)
    body = [
        "  ".join(str(row.get(key, "")).ljust(widths[key]) for key, _ in columns)
        for row in rows
    ]
    return "\n".join([header, divider, *body])


def write_output(rows: list[dict], output_format: str, output_path: str | None) -> None:
    if output_format == "json":
        content = json.dumps(rows, indent=2)
        if output_path:
            Path(output_path).write_text(content + "\n", encoding="utf-8")
        else:
            print(content)
        return

    if output_format == "csv":
        if output_path:
            with Path(output_path).open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
        else:
            writer = csv.DictWriter(sys.stdout, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        return

    content = render_table(rows)
    if output_path:
        Path(output_path).write_text(content + "\n", encoding="utf-8")
    else:
        print(content)


def resolve_symbols_file(direct_symbols: list[str], symbols_file: str | None) -> str | None:
    if symbols_file:
        return symbols_file
    if not direct_symbols and DEFAULT_SYMBOLS_FILE.exists():
        return str(DEFAULT_SYMBOLS_FILE)
    return None


def maybe_launch_picker(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    stocks_file: Path | None = None,
) -> bool:
    if args.symbols:
        return False

    stocks_file = stocks_file or (
        Path(args.symbols_file) if args.symbols_file else DEFAULT_SYMBOLS_FILE
    )
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        if stocks_file.exists():
            parser.error(
                f"no symbols found in {stocks_file}; run nse-stock-picker to populate it"
            )
        parser.error(f"{stocks_file} does not exist; run nse-stock-picker to create it")

    if stocks_file.exists():
        print(f"No symbols found in {stocks_file}. Launching nse-stock-picker...")
    else:
        print(f"{stocks_file} does not exist. Launching nse-stock-picker...")

    try:
        venv_python = PROJECT_DIR / ".venv" / "bin" / "python"
        if venv_python.exists():
            result = subprocess.run(
                [
                    str(venv_python),
                    str(PROJECT_DIR / "nse_stock_picker.py"),
                    "--stocks-file",
                    str(stocks_file),
                    "--timeout",
                    str(args.timeout),
                ],
                check=False,
            )
            return result.returncode == 0

        from nse_stock_picker import run_picker_app

        return run_picker_app(stocks_file=stocks_file, timeout=args.timeout) == 0
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return False
    except requests.RequestException as exc:
        print(f"Failed to load NSE symbol list: {exc}", file=sys.stderr)
        return False
    except OSError as exc:
        print(f"Failed to launch nse-stock-picker: {exc}", file=sys.stderr)
        return False


def symbol_output_path(out_dir: Path, symbol: str) -> Path:
    return out_dir / "stocks" / f"{symbol}.csv"


def read_last_saved_date(csv_path: Path) -> date | None:
    if not csv_path.exists():
        return None

    last_value = ""
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("date"):
                last_value = row["date"]

    if not last_value:
        return None
    return date.fromisoformat(last_value)


def load_saved_dates(csv_path: Path) -> set[str]:
    if not csv_path.exists():
        return set()

    saved_dates: set[str] = set()
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row_date = row.get("date", "")
            if row_date:
                saved_dates.add(row_date)
    return saved_dates


def deduplicate_symbol_file(csv_path: Path) -> int:
    if not csv_path.exists():
        return 0

    deduplicated_rows: list[dict[str, str]] = []
    seen_dates: set[str] = set()
    removed_rows = 0

    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row_date = row.get("date", "")
            if row_date and row_date in seen_dates:
                removed_rows += 1
                continue
            if row_date:
                seen_dates.add(row_date)
            deduplicated_rows.append(row)

    if removed_rows == 0:
        return 0

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(deduplicated_rows)

    return removed_rows


def append_symbol_rows(csv_path: Path, rows: list[dict], saved_dates: set[str]) -> int:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = csv_path.exists()
    fresh_rows: list[dict] = []
    for row in rows:
        row_date = row.get("date", "")
        if row_date and row_date in saved_dates:
            continue
        if row_date:
            saved_dates.add(row_date)
        fresh_rows.append(row)

    if not fresh_rows:
        return 0

    with csv_path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(fresh_rows)

    return len(fresh_rows)


def write_progress(progress_path: Path, payload: dict) -> None:
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def update_symbol_files(
    client: NSEArchiveClient,
    symbols: list[str],
    series: str,
    start_date: date,
    end_date: date,
    out_dir: Path,
    resume: bool,
    log_every: int,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    logger = RunLogger(out_dir)
    progress_path = out_dir / "progress.json"
    stocks_dir = out_dir / "stocks"
    stocks_dir.mkdir(parents=True, exist_ok=True)

    symbol_start_dates: dict[str, date] = {}
    symbol_saved_dates: dict[str, set[str]] = {}
    for symbol in symbols:
        csv_path = symbol_output_path(out_dir, symbol)
        removed_rows = deduplicate_symbol_file(csv_path)
        if removed_rows:
            logger.info(f"{symbol}: removed {removed_rows} duplicate rows from existing output")

        symbol_saved_dates[symbol] = load_saved_dates(csv_path)
        effective_start = start_date
        last_saved = read_last_saved_date(csv_path) if resume else None

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
            "symbols": symbols,
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
        "symbols": symbols,
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

    logger.info(
        f"Starting update for {len(symbol_start_dates)} symbols into {out_dir}"
    )

    rows_written = 0
    for index, day in enumerate(processing_days, start=1):
        active_symbols = {
            symbol for symbol, symbol_start in symbol_start_dates.items() if symbol_start <= day
        }
        day_rows = client.fetch_day(day, active_symbols, series)

        rows_by_symbol: dict[str, list[dict]] = {}
        for row in day_rows:
            rows_by_symbol.setdefault(row["symbol"], []).append(row)

        for symbol, rows in rows_by_symbol.items():
            written_rows = append_symbol_rows(
                symbol_output_path(out_dir, symbol),
                rows,
                symbol_saved_dates.setdefault(symbol, set()),
            )
            progress["symbol_rows_written"][symbol] += written_rows
            rows_written += written_rows

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
    logger.info(f"Finished update with {rows_written} rows written")
    return rows_written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pull daily NSE historical equity data for the stocks you want."
    )
    parser.add_argument(
        "symbols",
        nargs="*",
        help="NSE symbols like RELIANCE TCS INFY or a comma-separated list",
    )
    parser.add_argument(
        "--symbols-file",
        help="Text file with symbols, one per line or comma-separated. Defaults to stocks.txt in the project directory.",
    )
    parser.add_argument(
        "--start-date",
        type=parse_date,
        default=DEFAULT_START_DATE,
        help="Start date in YYYY-MM-DD format (default: 2008-01-01)",
    )
    parser.add_argument(
        "--end-date",
        type=parse_date,
        default=date.today(),
        help="End date in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--series",
        default="EQ",
        help="NSE series to pull (default: EQ)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Concurrent archive downloads for combined output mode (default: 4)",
    )
    parser.add_argument(
        "--requests-per-second",
        type=float,
        default=3.0,
        help="Global request throttle across all requests (default: 3.0)",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json", "csv"),
        default="csv",
        help="Output format (default: csv)",
    )
    parser.add_argument(
        "--output",
        help="Optional combined output file path. Without this, CSV runs update one file per symbol under out/stocks/.",
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
    stocks_file = Path(args.symbols_file) if args.symbols_file else DEFAULT_SYMBOLS_FILE
    picker_ran = False
    picker_attempted = False

    if not args.symbols and not stocks_file.exists():
        picker_attempted = True
        picker_ran = maybe_launch_picker(parser, args, stocks_file)

    symbols_file = resolve_symbols_file(args.symbols, args.symbols_file)

    try:
        symbols = load_symbols(args.symbols, symbols_file)
    except OSError as exc:
        if args.symbols or symbols_file is None or Path(symbols_file).exists():
            print(f"Could not read symbols file: {exc}", file=sys.stderr)
            return 1
        symbols = []

    if not symbols:
        if not picker_attempted:
            picker_attempted = True
            picker_ran = maybe_launch_picker(parser, args, stocks_file)
        symbols_file = resolve_symbols_file(args.symbols, args.symbols_file)
        try:
            symbols = load_symbols(args.symbols, symbols_file)
        except OSError as exc:
            if args.symbols or symbols_file is None or Path(symbols_file).exists():
                print(f"Could not read symbols file: {exc}", file=sys.stderr)
                return 1
            symbols = []

        if not symbols:
            if picker_attempted:
                if picker_ran:
                    print("No symbols selected. Nothing to pull.", file=sys.stderr)
                return 1
            parser.error("provide at least one symbol or use --symbols-file")

    if args.start_date > args.end_date:
        parser.error("--start-date must be on or before --end-date")

    if args.workers < 1:
        parser.error("--workers must be at least 1")

    if args.requests_per_second < 0:
        parser.error("--requests-per-second must be 0 or greater")

    if args.log_every < 1:
        parser.error("--log-every must be at least 1")

    client = NSEArchiveClient(
        timeout=args.timeout,
        requests_per_second=args.requests_per_second,
    )

    if args.output is None and args.format == "csv":
        try:
            rows_written = update_symbol_files(
                client=client,
                symbols=symbols,
                series=args.series.upper(),
                start_date=args.start_date,
                end_date=args.end_date,
                out_dir=Path(args.out_dir),
                resume=args.resume,
                log_every=args.log_every,
            )
        except requests.RequestException as exc:
            print(f"Failed to download NSE archive data: {exc}", file=sys.stderr)
            return 1

        if rows_written == 0:
            return 0
        return 0

    try:
        rows = client.fetch_history(
            symbols=symbols,
            series=args.series.upper(),
            start_date=args.start_date,
            end_date=args.end_date,
            workers=args.workers,
        )
    except requests.RequestException as exc:
        print(f"Failed to download NSE archive data: {exc}", file=sys.stderr)
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
