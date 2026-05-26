#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import requests

from nse_puller import (
    DEFAULT_HEADERS,
    DEFAULT_OUT_DIR,
    DEFAULT_SYMBOLS_FILE,
    NSEArchiveClient,
    PROJECT_DIR,
    RunLogger,
    append_symbol_rows,
    iter_weekdays,
    load_symbols,
    normalize_history_row,
    normalize_timestamp,
    parse_date,
    resolve_symbols_file,
    write_progress,
)


DEFAULT_START_DATE = date(2008, 1, 1)
STATUS_CACHE_DIR = PROJECT_DIR / ".cache" / "nse-sources-status"
SOURCE_CACHE_DIR = PROJECT_DIR / ".cache" / "nse-sources"
MAX_PROGRESS_ERRORS = 20


@dataclass(frozen=True)
class ReportSpec:
    key: str
    filename_template: str
    url_template: str
    out_dir: str
    start_date: date

    def filename(self, day: date) -> str:
        return self.filename_template.format(ddmmyy=day.strftime("%d%m%y"), ddmmyyyy=day.strftime("%d%m%Y"))

    def url(self, day: date) -> str:
        return self.url_template.format(ddmmyy=day.strftime("%d%m%y"), ddmmyyyy=day.strftime("%d%m%Y"))


DELIVERY_SPEC = ReportSpec(
    key="delivery-positions",
    filename_template="MTO_{ddmmyyyy}.DAT",
    url_template="https://nsearchives.nseindia.com/archives/equities/mto/MTO_{ddmmyyyy}.DAT",
    out_dir="delivery-positions",
    start_date=date(2008, 1, 1),
)

VOLATILITY_SPEC = ReportSpec(
    key="daily-volatility",
    filename_template="CMVOLT_{ddmmyyyy}.CSV",
    url_template="https://nsearchives.nseindia.com/archives/nsccl/volt/CMVOLT_{ddmmyyyy}.CSV",
    out_dir="daily-volatility",
    start_date=date(2011, 4, 1),
)

FULL_SPEC = ReportSpec(
    key="full-bhavcopy-deliverable",
    filename_template="sec_bhavdata_full_{ddmmyyyy}.csv",
    url_template="https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv",
    out_dir="full-bhavcopy-deliverable",
    start_date=date(2019, 10, 1),
)

MARKET_ACTIVITY_SPEC = ReportSpec(
    key="market-activity",
    filename_template="MA{ddmmyy}.csv",
    url_template="https://nsearchives.nseindia.com/archives/equities/mkt/MA{ddmmyy}.csv",
    out_dir="market-activity",
    start_date=date(2012, 3, 1),
)


BHAVCOPY_START_DATE = date(2008, 1, 1)


class RateLimiter:
    def __init__(self, requests_per_second: float) -> None:
        self.interval = 0.0 if requests_per_second <= 0 else 1.0 / requests_per_second
        self.next_request_time = 0.0

    def wait(self) -> None:
        if self.interval == 0.0:
            return

        now = time.monotonic()
        if now < self.next_request_time:
            time.sleep(self.next_request_time - now)
            now = time.monotonic()
        self.next_request_time = now + self.interval


class SourceClient:
    def __init__(self, timeout: int = 30, requests_per_second: float = 4.0) -> None:
        self.timeout = timeout
        self.rate_limiter = RateLimiter(requests_per_second)

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


def status_cache_path(key: str, day: date) -> Path:
    return STATUS_CACHE_DIR / key / f"{day.isoformat()}.json"


def source_cache_path(spec: ReportSpec, day: date) -> Path:
    return SOURCE_CACHE_DIR / spec.key / f"{day:%Y}" / f"{day:%m}" / spec.filename(day)


def load_cached_status(key: str, day: date) -> dict | None:
    cache_path = status_cache_path(key, day)
    if not cache_path.exists():
        return None
    return json.loads(cache_path.read_text(encoding="utf-8"))


def write_cached_status(key: str, day: date, payload: dict) -> None:
    cache_path = status_cache_path(key, day)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def processed_cache_path(source: str, day: date) -> Path:
    return STATUS_CACHE_DIR / "processed" / source / f"{day.isoformat()}.json"


def is_source_processed(source: str, day: date) -> bool:
    return processed_cache_path(source, day).exists()


def mark_source_processed(source: str, day: date, output_path: Path, rows_written: int | None = None) -> None:
    cache_path = processed_cache_path(source, day)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "processed",
        "source": source,
        "date": day.isoformat(),
        "output_path": str(output_path),
        "output_exists": output_path.exists(),
        "checked_at": datetime.now().isoformat(),
    }
    if rows_written is not None:
        payload["rows_written"] = rows_written
    cache_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def should_process_source(source: str, day: date, output_path: Path) -> bool:
    return not output_path.exists() and not is_source_processed(source, day)


def fetch_source_bytes(client: SourceClient, spec: ReportSpec, day: date) -> bytes | None:
    source_cache = source_cache_path(spec, day)
    if source_cache.exists():
        return source_cache.read_bytes()

    cached_status = load_cached_status(spec.key, day)
    if cached_status and cached_status.get("status") == "missing":
        return None

    response = client.request_url(spec.url(day))
    if response.status_code == 404:
        write_cached_status(
            spec.key,
            day,
            {"status": "missing", "url": spec.url(day), "checked_at": datetime.now().isoformat()},
        )
        return None

    response.raise_for_status()
    source_cache.parent.mkdir(parents=True, exist_ok=True)
    source_cache.write_bytes(response.content)
    write_cached_status(
        spec.key,
        day,
        {
            "status": "downloaded",
            "url": spec.url(day),
            "cache_path": str(source_cache),
            "bytes": len(response.content),
            "checked_at": datetime.now().isoformat(),
        },
    )
    return response.content


def output_day_path(out_dir: Path, subdir: str, day: date) -> Path:
    return out_dir / subdir / f"{day:%Y}" / f"{day:%m}" / f"{day.isoformat()}.csv"


def output_market_activity_path(out_dir: Path, day: date) -> Path:
    return out_dir / MARKET_ACTIVITY_SPEC.out_dir / f"{day:%Y}" / f"{day:%m}" / MARKET_ACTIVITY_SPEC.filename(day)


def expected_output_paths(out_dir: Path, day: date) -> dict[str, Path]:
    paths: dict[str, Path] = {}

    if day >= BHAVCOPY_START_DATE:
        paths["bhavcopy"] = output_day_path(out_dir, "bhavcopy", day)
    if day >= DELIVERY_SPEC.start_date:
        paths[DELIVERY_SPEC.key] = output_day_path(out_dir, DELIVERY_SPEC.out_dir, day)
    if day >= VOLATILITY_SPEC.start_date:
        paths[VOLATILITY_SPEC.key] = output_day_path(out_dir, VOLATILITY_SPEC.out_dir, day)
    if day >= FULL_SPEC.start_date:
        paths[FULL_SPEC.key] = output_day_path(out_dir, FULL_SPEC.out_dir, day)
    if day >= MARKET_ACTIVITY_SPEC.start_date:
        paths[MARKET_ACTIVITY_SPEC.key] = output_market_activity_path(out_dir, day)

    return paths


def day_outputs_complete(day: date, expected_paths: dict[str, Path]) -> bool:
    return bool(expected_paths) and all(path.exists() or is_source_processed(source, day) for source, path in expected_paths.items())


def record_source_error(
    logger: RunLogger,
    progress: dict,
    source: str,
    day: date,
    exc: Exception,
    url: str | None = None,
    cache_path: Path | None = None,
) -> None:
    error = {
        "source": source,
        "date": day.isoformat(),
        "error_type": type(exc).__name__,
        "message": str(exc),
    }
    if url:
        error["url"] = url
    if cache_path:
        error["cache_path"] = str(cache_path)

    progress["errors_count"] += 1
    progress["recent_errors"].append(error)
    del progress["recent_errors"][:-MAX_PROGRESS_ERRORS]

    context = f" source={source} date={day.isoformat()}"
    if url:
        context += f" url={url}"
    if cache_path:
        context += f" cache={cache_path}"
    logger.info(f"ERROR{context}: {type(exc).__name__}: {exc}")


def parse_delivery_rows(day: date, payload: bytes, symbols: set[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in payload.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("20,"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 7:
            continue
        symbol = parts[2].strip().upper()
        if symbol not in symbols:
            continue
        rows.append(
            {
                "symbol": symbol,
                "series": parts[3],
                "date": day.isoformat(),
                "quantity_traded": parts[-3],
                "deliverable_quantity": parts[-2],
                "delivery_pct": parts[-1],
            }
        )
    return rows


def parse_volatility_rows(payload: bytes, symbols: set[str]) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8", errors="replace"), newline=""))
    rows: list[dict[str, str]] = []
    for raw_row in reader:
        symbol = (raw_row.get("Symbol") or "").strip().upper()
        if symbol not in symbols:
            continue
        rows.append(
            {
                "symbol": symbol,
                "date": normalize_timestamp(raw_row.get("Date", "")),
                "underlying_close_price": (raw_row.get("Underlying Close Price (A)") or "").strip(),
                "underlying_previous_close_price": (raw_row.get("Underlying Previous Day Close Price (B)") or "").strip(),
                "underlying_log_returns": (raw_row.get("Underlying Log Returns (C) = LN(A/B)") or "").strip(),
                "previous_day_underlying_volatility": (raw_row.get("Previous Day Underlying Volatility (D)") or "").strip(),
                "current_day_underlying_daily_volatility": (raw_row.get("Current Day Underlying Daily Volatility (E) = Sqrt(0.995*D*D + 0.005*C*C)") or "").strip(),
                "underlying_annualised_volatility": (raw_row.get("Underlying Annualised Volatility (F) = E*Sqrt(365)") or "").strip(),
            }
        )
    return rows


def parse_full_rows(payload: bytes, symbols: set[str]) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8", errors="replace"), newline=""), skipinitialspace=True)
    rows: list[dict[str, str]] = []
    for raw_row in reader:
        symbol = (raw_row.get("SYMBOL") or "").strip().upper()
        if symbol not in symbols:
            continue
        rows.append(
            {
                "symbol": symbol,
                "series": (raw_row.get("SERIES") or "").strip(),
                "date": normalize_timestamp(raw_row.get("DATE1", "")),
                "previous_close": (raw_row.get("PREV_CLOSE") or "").strip(),
                "open_price": (raw_row.get("OPEN_PRICE") or "").strip(),
                "high_price": (raw_row.get("HIGH_PRICE") or "").strip(),
                "low_price": (raw_row.get("LOW_PRICE") or "").strip(),
                "last_price": (raw_row.get("LAST_PRICE") or "").strip(),
                "close_price": (raw_row.get("CLOSE_PRICE") or "").strip(),
                "avg_price": (raw_row.get("AVG_PRICE") or "").strip(),
                "ttl_trd_qnty": (raw_row.get("TTL_TRD_QNTY") or "").strip(),
                "turnover_lacs": (raw_row.get("TURNOVER_LACS") or "").strip(),
                "no_of_trades": (raw_row.get("NO_OF_TRADES") or "").strip(),
                "deliv_qty": (raw_row.get("DELIV_QTY") or "").strip(),
                "deliv_per": (raw_row.get("DELIV_PER") or "").strip(),
            }
        )
    return rows


def write_day_csv(path: Path, rows: list[dict[str, str]]) -> bool:
    if not rows:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return True


def append_bhavcopy_rows(out_dir: Path, rows: list[dict[str, str]]) -> int:
    rows_by_symbol: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        rows_by_symbol.setdefault(row["symbol"], []).append(row)
    for symbol, symbol_rows in rows_by_symbol.items():
        append_symbol_rows(out_dir / "stocks" / f"{symbol}.csv", symbol_rows)
    return len(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download NSE source data for the symbols in stocks.txt with incremental skips for existing days and cached misses."
    )
    parser.add_argument(
        "symbols",
        nargs="*",
        help="Optional NSE symbols like SBIN LT TRENT or a comma-separated list",
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
        "--out-dir",
        default=str(DEFAULT_OUT_DIR),
        help="Project-local output directory for downloaded files, logs, and progress (default: ./out)",
    )
    parser.add_argument(
        "--requests-per-second",
        type=float,
        default=4.0,
        help="Global request throttle across all requests (default: 4.0)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--log-every",
        type=int,
        default=50,
        help="Progress log interval in trading days (default: 50)",
    )
    return parser


def run() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.start_date > args.end_date:
        parser.error("--start-date must be on or before --end-date")
    if args.requests_per_second < 0:
        parser.error("--requests-per-second must be 0 or greater")
    if args.log_every < 1:
        parser.error("--log-every must be at least 1")

    symbols_file = resolve_symbols_file(args.symbols, args.symbols_file)
    if not args.symbols and symbols_file is None and DEFAULT_SYMBOLS_FILE.exists():
        symbols_file = str(DEFAULT_SYMBOLS_FILE)

    try:
        symbols = load_symbols(args.symbols, symbols_file)
    except OSError as exc:
        print(f"Could not read symbols file: {exc}", file=sys.stderr)
        return 1

    if not symbols:
        parser.error("provide at least one symbol or use --symbols-file")

    symbol_set = set(symbols)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    logger = RunLogger(out_dir)
    progress_path = out_dir / "nse-sources-progress.json"
    processing_days = list(iter_weekdays(args.start_date, args.end_date))

    source_client = SourceClient(timeout=args.timeout, requests_per_second=args.requests_per_second)
    bhavcopy_client = NSEArchiveClient(timeout=args.timeout, requests_per_second=args.requests_per_second)

    progress = {
        "status": "running",
        "started_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "requested_start_date": args.start_date.isoformat(),
        "requested_end_date": args.end_date.isoformat(),
        "symbols": sorted(symbols),
        "days_processed": 0,
        "days_total": len(processing_days),
        "last_processed_day": None,
        "totals": {
            "bhavcopy_rows": 0,
            "delivery_days_written": 0,
            "volatility_days_written": 0,
            "full_days_written": 0,
            "market_activity_days_written": 0,
        },
        "errors_count": 0,
        "recent_errors": [],
    }
    write_progress(progress_path, progress)
    logger.info(f"Starting NSE source pull for {len(symbols)} symbols into {out_dir}")

    for index, day in enumerate(processing_days, start=1):
        day_output_paths = expected_output_paths(out_dir, day)
        if day_outputs_complete(day, day_output_paths):
            progress["updated_at"] = datetime.now().isoformat()
            progress["days_processed"] = index
            progress["last_processed_day"] = day.isoformat()
            write_progress(progress_path, progress)

            if index == 1 or index == len(processing_days) or index % args.log_every == 0:
                logger.info(
                    f"Skipped {day.isoformat()}: all sources already handled ({index}/{len(processing_days)})"
                )
            continue

        bhavcopy_output = day_output_paths.get("bhavcopy") or output_day_path(out_dir, "bhavcopy", day)
        if day >= BHAVCOPY_START_DATE and should_process_source("bhavcopy", day, bhavcopy_output):
            try:
                day_rows = bhavcopy_client.fetch_day(day, symbol_set, "EQ")
                if write_day_csv(bhavcopy_output, day_rows):
                    progress["totals"]["bhavcopy_rows"] += append_bhavcopy_rows(out_dir, day_rows)
                mark_source_processed("bhavcopy", day, bhavcopy_output, len(day_rows))
            except (OSError, ValueError, csv.Error, requests.RequestException, zipfile.BadZipFile) as exc:
                record_source_error(logger, progress, "bhavcopy", day, exc, cache_path=bhavcopy_client.cache_path(day))

        delivery_output = day_output_paths.get(DELIVERY_SPEC.key) or output_day_path(out_dir, DELIVERY_SPEC.out_dir, day)
        if day >= DELIVERY_SPEC.start_date and should_process_source(DELIVERY_SPEC.key, day, delivery_output):
            try:
                delivery_payload = fetch_source_bytes(source_client, DELIVERY_SPEC, day)
                delivery_rows = parse_delivery_rows(day, delivery_payload, symbol_set) if delivery_payload else []
                if write_day_csv(delivery_output, delivery_rows):
                    progress["totals"]["delivery_days_written"] += 1
                mark_source_processed(DELIVERY_SPEC.key, day, delivery_output, len(delivery_rows))
            except (OSError, ValueError, csv.Error, requests.RequestException) as exc:
                record_source_error(
                    logger,
                    progress,
                    DELIVERY_SPEC.key,
                    day,
                    exc,
                    url=DELIVERY_SPEC.url(day),
                    cache_path=source_cache_path(DELIVERY_SPEC, day),
                )

        volatility_output = day_output_paths.get(VOLATILITY_SPEC.key) or output_day_path(out_dir, VOLATILITY_SPEC.out_dir, day)
        if day >= VOLATILITY_SPEC.start_date and should_process_source(VOLATILITY_SPEC.key, day, volatility_output):
            try:
                volatility_payload = fetch_source_bytes(source_client, VOLATILITY_SPEC, day)
                volatility_rows = parse_volatility_rows(volatility_payload, symbol_set) if volatility_payload else []
                if write_day_csv(volatility_output, volatility_rows):
                    progress["totals"]["volatility_days_written"] += 1
                mark_source_processed(VOLATILITY_SPEC.key, day, volatility_output, len(volatility_rows))
            except (OSError, ValueError, csv.Error, requests.RequestException) as exc:
                record_source_error(
                    logger,
                    progress,
                    VOLATILITY_SPEC.key,
                    day,
                    exc,
                    url=VOLATILITY_SPEC.url(day),
                    cache_path=source_cache_path(VOLATILITY_SPEC, day),
                )

        full_output = day_output_paths.get(FULL_SPEC.key) or output_day_path(out_dir, FULL_SPEC.out_dir, day)
        if day >= FULL_SPEC.start_date and should_process_source(FULL_SPEC.key, day, full_output):
            try:
                full_payload = fetch_source_bytes(source_client, FULL_SPEC, day)
                full_rows = parse_full_rows(full_payload, symbol_set) if full_payload else []
                if write_day_csv(full_output, full_rows):
                    progress["totals"]["full_days_written"] += 1
                mark_source_processed(FULL_SPEC.key, day, full_output, len(full_rows))
            except (OSError, ValueError, csv.Error, requests.RequestException) as exc:
                record_source_error(
                    logger,
                    progress,
                    FULL_SPEC.key,
                    day,
                    exc,
                    url=FULL_SPEC.url(day),
                    cache_path=source_cache_path(FULL_SPEC, day),
                )

        market_activity_output = day_output_paths.get(MARKET_ACTIVITY_SPEC.key) or output_market_activity_path(out_dir, day)
        if day >= MARKET_ACTIVITY_SPEC.start_date and should_process_source(
            MARKET_ACTIVITY_SPEC.key, day, market_activity_output
        ):
            try:
                market_activity_payload = fetch_source_bytes(source_client, MARKET_ACTIVITY_SPEC, day)
                if market_activity_payload:
                    market_activity_output.parent.mkdir(parents=True, exist_ok=True)
                    market_activity_output.write_bytes(market_activity_payload)
                    progress["totals"]["market_activity_days_written"] += 1
                mark_source_processed(
                    MARKET_ACTIVITY_SPEC.key,
                    day,
                    market_activity_output,
                    1 if market_activity_payload else 0,
                )
            except (OSError, ValueError, csv.Error, requests.RequestException) as exc:
                record_source_error(
                    logger,
                    progress,
                    MARKET_ACTIVITY_SPEC.key,
                    day,
                    exc,
                    url=MARKET_ACTIVITY_SPEC.url(day),
                    cache_path=source_cache_path(MARKET_ACTIVITY_SPEC, day),
                )

        progress["updated_at"] = datetime.now().isoformat()
        progress["days_processed"] = index
        progress["last_processed_day"] = day.isoformat()
        write_progress(progress_path, progress)

        if index == 1 or index == len(processing_days) or index % args.log_every == 0:
            logger.info(f"Processed {index}/{len(processing_days)} trading days through {day.isoformat()}")

    progress["status"] = "completed"
    progress["updated_at"] = datetime.now().isoformat()
    write_progress(progress_path, progress)
    logger.info("Finished NSE source pull")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
