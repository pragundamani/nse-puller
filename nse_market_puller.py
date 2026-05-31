#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from nse_puller import (
    DEFAULT_OUT_DIR,
    PROJECT_DIR,
    RunLogger,
    iter_weekdays,
    load_symbols,
    normalize_timestamp,
    parse_date,
    resolve_symbols_file,
    write_progress,
)

DEFAULT_START_DATE = date(2008, 1, 1)
CACHE_DIR = PROJECT_DIR / ".cache" / "nse-market-features"
STATUS_CACHE_DIR = PROJECT_DIR / ".cache" / "nse-market-features-status"
DEFAULT_OPTION_INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "NIFTYIT"]
NSE_BASE_URL = "https://www.nseindia.com"
PRIME_URLS = [
    f"{NSE_BASE_URL}/companies-listing/corporate-filings-announcements",
    f"{NSE_BASE_URL}/companies-listing/corporate-filings-actions",
    f"{NSE_BASE_URL}/option-chain",
]
HTML_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
}
JSON_HEADERS = {
    "User-Agent": HTML_HEADERS["User-Agent"],
    "Accept": "application/json, text/plain, */*",
}
ANNOUNCEMENT_FIELDS = [
    "symbol",
    "announcement_date",
    "announcement_timestamp",
    "description",
    "attachment_url",
    "attachment_text",
    "company_name",
    "isin",
    "industry",
    "sequence_id",
    "source",
]
ACTION_FIELDS = [
    "symbol",
    "series",
    "ex_date",
    "record_date",
    "book_closure_start",
    "book_closure_end",
    "nomination_start",
    "nomination_end",
    "subject",
    "face_value",
    "company_name",
    "isin",
    "source",
]
OPTION_FIELDS = [
    "symbol",
    "date",
    "timestamp",
    "is_index",
    "expiry",
    "underlying_value",
    "atm_strike",
    "total_call_oi",
    "total_put_oi",
    "put_call_ratio",
    "contract_rows",
    "source",
]


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


class NSEMarketClient:
    def __init__(self, timeout: int = 30, requests_per_second: float = 0.0) -> None:
        self.timeout = timeout
        self.rate_limiter = RateLimiter(requests_per_second)
        self.session = requests.Session()
        self._primed = False

    def prime_session(self) -> None:
        if self._primed:
            return
        last_error: requests.RequestException | None = None
        for url in PRIME_URLS:
            try:
                response = self.session.get(
                    url, headers=HTML_HEADERS, timeout=self.timeout
                )
                response.raise_for_status()
                self._primed = True
                return
            except requests.RequestException as exc:
                last_error = exc
        if last_error is not None:
            raise last_error

    def request_json(self, url: str, referer: str) -> list | dict:
        self.prime_session()
        last_response: requests.Response | None = None
        last_error: requests.RequestException | None = None
        headers = dict(JSON_HEADERS)
        headers["Referer"] = referer

        for attempt in range(8):
            self.rate_limiter.wait()
            try:
                response = self.session.get(url, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
            last_response = response

            if response.status_code not in {403, 429, 500, 502, 503, 504}:
                response.raise_for_status()
                return response.json()

            if attempt < 3:
                time.sleep(1.5 * (attempt + 1))

        if last_error is not None:
            raise last_error
        assert last_response is not None
        
        # If we got here with a rate-limit or server error after retries, raise it
        import sys
        print(f"Warning: API returned {last_response.status_code} after {attempt + 1} attempts", file=sys.stderr)
        last_response.raise_for_status()
        # Fallback: if raise_for_status() didn't raise (e.g., 2xx response with empty body)
        try:
            return last_response.json()
        except ValueError:
            # JSON parsing failed, return empty dict
            return {}

    def fetch_announcements(
        self, symbol: str, start_date: date, end_date: date
    ) -> list[dict]:
        url = (
            f"{NSE_BASE_URL}/api/corporate-announcements?index=equities"
            f"&from_date={start_date:%d-%m-%Y}&to_date={end_date:%d-%m-%Y}&symbol={symbol}"
        )
        return self.request_json(
            url, f"{NSE_BASE_URL}/companies-listing/corporate-filings-announcements"
        )

    def fetch_actions(
        self, symbol: str, start_date: date, end_date: date
    ) -> list[dict]:
        url = (
            f"{NSE_BASE_URL}/api/corporates-corporateActions?index=equities"
            f"&from_date={start_date:%d-%m-%Y}&to_date={end_date:%d-%m-%Y}&symbol={symbol}"
        )
        return self.request_json(
            url, f"{NSE_BASE_URL}/companies-listing/corporate-filings-actions"
        )

    def fetch_option_chain(self, symbol: str, is_index: bool) -> dict:
        if is_index:
            url = f"{NSE_BASE_URL}/api/option-chain-v3?type=Indices&symbol={symbol}&expiry=latest"
        else:
            url = f"{NSE_BASE_URL}/api/option-chain-v3?type=Equity&symbol={symbol}&expiry=latest"
        try:
            payload = self.request_json(url, f"{NSE_BASE_URL}/option-chain")
            return payload if isinstance(payload, dict) else {}
        except requests.RequestException as exc:
            # Log the error for debugging
            import sys
            print(f"Warning: Failed to fetch option chain for {symbol}: {exc}", file=sys.stderr)
            return {}


def status_cache_path(domain: str, identifier: str) -> Path:
    return STATUS_CACHE_DIR / domain / f"{identifier}.json"


def load_cached_status(domain: str, identifier: str) -> dict | None:
    path = status_cache_path(domain, identifier)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_cached_status(domain: str, identifier: str, payload: dict) -> None:
    path = status_cache_path(domain, identifier)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def cache_payload(path: Path, payload: list | dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load_existing_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        return []

    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_last_saved_date(csv_path: Path, fieldname: str) -> date | None:
    last_value = ""
    for row in load_existing_rows(csv_path):
        value = (row.get(fieldname) or "").strip()
        if value:
            last_value = value

    if not last_value:
        return None
    return date.fromisoformat(last_value)


def merge_rows(
    csv_path: Path,
    fieldnames: list[str],
    rows: list[dict],
    key_fields: tuple[str, ...],
    sort_fields: tuple[str, ...],
) -> int:
    if not rows and csv_path.exists():
        return 0

    merged: dict[tuple[str, ...], dict[str, str]] = {}
    for existing_row in load_existing_rows(csv_path):
        key = tuple(existing_row.get(field, "") for field in key_fields)
        merged[key] = {field: existing_row.get(field, "") for field in fieldnames}

    added = 0
    for row in rows:
        normalized_row = {field: str(row.get(field, "")) for field in fieldnames}
        key = tuple(normalized_row.get(field, "") for field in key_fields)
        if key not in merged:
            added += 1
        merged[key] = normalized_row

    ordered_rows = sorted(
        merged.values(),
        key=lambda row: tuple(row.get(field, "") for field in sort_fields),
    )
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with temp_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(ordered_rows)
    temp_path.replace(csv_path)
    return added


def normalize_nse_date(value: str) -> str:
    value = value.strip()
    if not value or value == "-":
        return ""
    return normalize_timestamp(value)


def normalize_announcement_row(raw_row: dict) -> dict:
    timestamp = (raw_row.get("sort_date") or "").strip()
    announcement_date = ""
    if timestamp:
        try:
            announcement_date = (
                datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S").date().isoformat()
            )
        except ValueError:
            announcement_date = normalize_timestamp(timestamp)
    return {
        "symbol": (raw_row.get("symbol") or "").strip().upper(),
        "announcement_date": announcement_date,
        "announcement_timestamp": timestamp,
        "description": (raw_row.get("desc") or "").strip(),
        "attachment_url": (raw_row.get("attchmntFile") or "").strip(),
        "attachment_text": (raw_row.get("attchmntText") or "").strip(),
        "company_name": (raw_row.get("sm_name") or "").strip(),
        "isin": (raw_row.get("sm_isin") or "").strip(),
        "industry": (raw_row.get("smIndustry") or "").strip(),
        "sequence_id": str(raw_row.get("seq_id") or "").strip(),
        "source": "nse",
    }


def normalize_action_row(raw_row: dict) -> dict:
    return {
        "symbol": (raw_row.get("symbol") or "").strip().upper(),
        "series": (raw_row.get("series") or "").strip(),
        "ex_date": normalize_nse_date(raw_row.get("exDate") or ""),
        "record_date": normalize_nse_date(raw_row.get("recDate") or ""),
        "book_closure_start": normalize_nse_date(raw_row.get("bcStartDate") or ""),
        "book_closure_end": normalize_nse_date(raw_row.get("bcEndDate") or ""),
        "nomination_start": normalize_nse_date(raw_row.get("ndStartDate") or ""),
        "nomination_end": normalize_nse_date(raw_row.get("ndEndDate") or ""),
        "subject": (raw_row.get("subject") or "").strip(),
        "face_value": str(raw_row.get("faceVal") or "").strip(),
        "company_name": (raw_row.get("comp") or "").strip(),
        "isin": (raw_row.get("isin") or "").strip(),
        "source": "nse",
    }


def summarize_option_chain(
    payload: dict, symbol: str, is_index: bool, snapshot_date: date
) -> list[dict]:
    if not payload:
        return []
    
    records = payload.get("records")
    if not isinstance(records, dict):
        return []

    data_rows = records.get("data")
    if not isinstance(data_rows, list) or not data_rows:
        return []

    underlying_value = records.get("underlyingValue")
    try:
        underlying_float = float(underlying_value)
    except (TypeError, ValueError):
        underlying_float = None

    expiry_dates = records.get("expiryDates")
    selected_expiry = (
        expiry_dates[0] if isinstance(expiry_dates, list) and expiry_dates else ""
    )
    filtered_rows = [
        row for row in data_rows if row.get("expiryDate") == selected_expiry
    ] or data_rows

    total_call_oi = 0.0
    total_put_oi = 0.0
    atm_strike = ""
    best_distance: float | None = None

    for row in filtered_rows:
        strike_price = row.get("strikePrice")
        try:
            strike_float = float(strike_price)
        except (TypeError, ValueError):
            strike_float = None

        ce = row.get("CE") or {}
        pe = row.get("PE") or {}
        total_call_oi += float(ce.get("openInterest") or 0)
        total_put_oi += float(pe.get("openInterest") or 0)

        if underlying_float is not None and strike_float is not None:
            distance = abs(strike_float - underlying_float)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                atm_strike = str(strike_price)

    put_call_ratio = ""
    if total_call_oi > 0:
        put_call_ratio = f"{(total_put_oi / total_call_oi):.6f}"

    return [
        {
            "symbol": symbol,
            "date": snapshot_date.isoformat(),
            "timestamp": str(
                records.get("timestamp") or payload.get("serverTime") or ""
            ).strip(),
            "is_index": "1" if is_index else "0",
            "expiry": str(selected_expiry).strip(),
            "underlying_value": str(underlying_value or "").strip(),
            "atm_strike": atm_strike,
            "total_call_oi": f"{total_call_oi:.0f}",
            "total_put_oi": f"{total_put_oi:.0f}",
            "put_call_ratio": put_call_ratio,
            "contract_rows": str(len(filtered_rows)),
            "source": "nse",
        }
    ]


def announcement_output_path(out_dir: Path, symbol: str) -> Path:
    return out_dir / "corporate-announcements" / f"{symbol}.csv"


def action_output_path(out_dir: Path, symbol: str) -> Path:
    return out_dir / "corporate-actions" / f"{symbol}.csv"


def option_output_path(out_dir: Path, symbol: str) -> Path:
    return out_dir / "option-chain" / f"{symbol}.csv"


def announcement_cache_path(symbol: str, day: date) -> Path:
    return CACHE_DIR / "corporate-announcements" / symbol / f"{day.isoformat()}.json"


def action_cache_path(symbol: str, day: date) -> Path:
    return CACHE_DIR / "corporate-actions" / symbol / f"{day.isoformat()}.json"


def option_cache_path(symbol: str, snapshot_date: date) -> Path:
    return CACHE_DIR / "option-chain" / symbol / f"{snapshot_date.isoformat()}.json"


def iter_option_underlyings(
    symbols: list[str], include_indices: bool, option_indices: list[str]
) -> list[tuple[str, bool]]:
    underlyings = [(symbol, False) for symbol in symbols]
    if include_indices:
        underlyings.extend((index_name.upper(), True) for index_name in option_indices)
    return underlyings


def day_status_id(symbol: str, day: date) -> str:
    return f"{symbol}-{day.isoformat()}"


def update_announcements(
    client: NSEMarketClient,
    symbols: list[str],
    start_date: date,
    end_date: date,
    out_dir: Path,
    resume: bool,
    log_every: int,
) -> int:
    logger = RunLogger(out_dir)
    progress_path = out_dir / "market-announcements-progress.json"

    symbol_start_dates: dict[str, date] = {}
    for symbol in symbols:
        effective_start = start_date
        if resume:
            last_saved = read_last_saved_date(
                announcement_output_path(out_dir, symbol), "announcement_date"
            )
            if last_saved is not None:
                candidate_start = last_saved + timedelta(days=1)
                if candidate_start > effective_start:
                    effective_start = candidate_start
                logger.info(
                    f"{symbol}: announcements resume after {last_saved.isoformat()}"
                )
        if effective_start <= end_date:
            symbol_start_dates[symbol] = effective_start
        else:
            logger.info(f"{symbol}: announcements already up to date")

    if not symbol_start_dates:
        payload = {
            "status": "completed",
            "started_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "requested_start_date": start_date.isoformat(),
            "requested_end_date": end_date.isoformat(),
            "symbols": symbols,
            "rows_written": 0,
            "days_total": 0,
            "units_total": 0,
            "units_processed": 0,
            "message": "All announcement rows were already up to date.",
        }
        write_progress(progress_path, payload)
        logger.info("All announcement rows are already up to date")
        return 0

    processing_days = list(iter_weekdays(min(symbol_start_dates.values()), end_date))
    total_units = sum(
        1
        for day in processing_days
        for symbol in symbol_start_dates
        if symbol_start_dates[symbol] <= day
    )

    progress = {
        "status": "running",
        "started_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "requested_start_date": start_date.isoformat(),
        "requested_end_date": end_date.isoformat(),
        "symbols": symbols,
        "active_symbols": sorted(symbol_start_dates),
        "symbol_start_dates": {
            symbol: symbol_start_dates[symbol].isoformat()
            for symbol in sorted(symbol_start_dates)
        },
        "days_total": len(processing_days),
        "units_total": total_units,
        "units_processed": 0,
        "last_processed_day": None,
        "rows_written": 0,
        "symbol_rows_written": {symbol: 0 for symbol in sorted(symbol_start_dates)},
    }
    write_progress(progress_path, progress)

    rows_written = 0
    processed_units = 0
    for day_index, day in enumerate(processing_days, start=1):
        active_symbols = [
            symbol
            for symbol, symbol_start in symbol_start_dates.items()
            if symbol_start <= day
        ]
        for symbol in active_symbols:
            status_id = day_status_id(symbol, day)
            if resume:
                cached_status = load_cached_status("corporate-announcements", status_id)
                if cached_status and cached_status.get("status") == "downloaded":
                    processed_units += 1
                    progress["units_processed"] = processed_units
                    progress["last_processed_day"] = day.isoformat()
                    progress["updated_at"] = datetime.now().isoformat()
                    continue

            cache_path = announcement_cache_path(symbol, day)
            payload = client.fetch_announcements(symbol, day, day)
            cache_payload(cache_path, payload)
            rows = [normalize_announcement_row(row) for row in payload]
            rows = [row for row in rows if row["announcement_date"] == day.isoformat()]
            added = merge_rows(
                announcement_output_path(out_dir, symbol),
                ANNOUNCEMENT_FIELDS,
                rows,
                ("symbol", "sequence_id"),
                ("announcement_date", "announcement_timestamp", "sequence_id"),
            )
            write_cached_status(
                "corporate-announcements",
                status_id,
                {
                    "status": "downloaded",
                    "symbol": symbol,
                    "date": day.isoformat(),
                    "cache_path": str(cache_path),
                    "records": len(payload),
                    "rows_added": added,
                    "checked_at": datetime.now().isoformat(),
                },
            )
            progress["rows_written"] += added
            progress["symbol_rows_written"][symbol] += added
            rows_written += added
            processed_units += 1
            progress["units_processed"] = processed_units
            progress["last_processed_day"] = day.isoformat()
            progress["updated_at"] = datetime.now().isoformat()

        write_progress(progress_path, progress)
        if (
            day_index == 1
            or day_index == len(processing_days)
            or day_index % log_every == 0
        ):
            logger.info(
                f"Announcements: processed {day_index}/{len(processing_days)} trading days through {day.isoformat()}"
            )

    progress["status"] = "completed"
    progress["updated_at"] = datetime.now().isoformat()
    write_progress(progress_path, progress)
    logger.info(f"Finished announcements update with {rows_written} rows written")
    return rows_written


def update_actions(
    client: NSEMarketClient,
    symbols: list[str],
    start_date: date,
    end_date: date,
    out_dir: Path,
    resume: bool,
    log_every: int,
) -> int:
    logger = RunLogger(out_dir)
    progress_path = out_dir / "market-actions-progress.json"

    symbol_start_dates: dict[str, date] = {}
    for symbol in symbols:
        effective_start = start_date
        if resume:
            last_saved = read_last_saved_date(
                action_output_path(out_dir, symbol), "ex_date"
            )
            if last_saved is not None:
                candidate_start = last_saved + timedelta(days=1)
                if candidate_start > effective_start:
                    effective_start = candidate_start
                logger.info(f"{symbol}: actions resume after {last_saved.isoformat()}")
        if effective_start <= end_date:
            symbol_start_dates[symbol] = effective_start
        else:
            logger.info(f"{symbol}: actions already up to date")

    if not symbol_start_dates:
        payload = {
            "status": "completed",
            "started_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "requested_start_date": start_date.isoformat(),
            "requested_end_date": end_date.isoformat(),
            "symbols": symbols,
            "rows_written": 0,
            "days_total": 0,
            "units_total": 0,
            "units_processed": 0,
            "message": "All corporate action rows were already up to date.",
        }
        write_progress(progress_path, payload)
        logger.info("All corporate action rows are already up to date")
        return 0

    processing_days = list(iter_weekdays(min(symbol_start_dates.values()), end_date))
    total_units = sum(
        1
        for day in processing_days
        for symbol in symbol_start_dates
        if symbol_start_dates[symbol] <= day
    )

    progress = {
        "status": "running",
        "started_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "requested_start_date": start_date.isoformat(),
        "requested_end_date": end_date.isoformat(),
        "symbols": symbols,
        "active_symbols": sorted(symbol_start_dates),
        "symbol_start_dates": {
            symbol: symbol_start_dates[symbol].isoformat()
            for symbol in sorted(symbol_start_dates)
        },
        "days_total": len(processing_days),
        "units_total": total_units,
        "units_processed": 0,
        "last_processed_day": None,
        "rows_written": 0,
        "symbol_rows_written": {symbol: 0 for symbol in sorted(symbol_start_dates)},
    }
    write_progress(progress_path, progress)

    rows_written = 0
    processed_units = 0
    for day_index, day in enumerate(processing_days, start=1):
        active_symbols = [
            symbol
            for symbol, symbol_start in symbol_start_dates.items()
            if symbol_start <= day
        ]
        for symbol in active_symbols:
            status_id = day_status_id(symbol, day)
            if resume:
                cached_status = load_cached_status("corporate-actions", status_id)
                if cached_status and cached_status.get("status") == "downloaded":
                    processed_units += 1
                    progress["units_processed"] = processed_units
                    progress["last_processed_day"] = day.isoformat()
                    progress["updated_at"] = datetime.now().isoformat()
                    continue

            cache_path = action_cache_path(symbol, day)
            payload = client.fetch_actions(symbol, day, day)
            cache_payload(cache_path, payload)
            rows = [normalize_action_row(row) for row in payload]
            rows = [row for row in rows if row["ex_date"] == day.isoformat()]
            added = merge_rows(
                action_output_path(out_dir, symbol),
                ACTION_FIELDS,
                rows,
                ("symbol", "ex_date", "subject"),
                ("ex_date", "record_date", "subject"),
            )
            write_cached_status(
                "corporate-actions",
                status_id,
                {
                    "status": "downloaded",
                    "symbol": symbol,
                    "date": day.isoformat(),
                    "cache_path": str(cache_path),
                    "records": len(payload),
                    "rows_added": added,
                    "checked_at": datetime.now().isoformat(),
                },
            )
            progress["rows_written"] += added
            progress["symbol_rows_written"][symbol] += added
            rows_written += added
            processed_units += 1
            progress["units_processed"] = processed_units
            progress["last_processed_day"] = day.isoformat()
            progress["updated_at"] = datetime.now().isoformat()

        write_progress(progress_path, progress)
        if (
            day_index == 1
            or day_index == len(processing_days)
            or day_index % log_every == 0
        ):
            logger.info(
                f"Actions: processed {day_index}/{len(processing_days)} trading days through {day.isoformat()}"
            )

    progress["status"] = "completed"
    progress["updated_at"] = datetime.now().isoformat()
    write_progress(progress_path, progress)
    logger.info(f"Finished actions update with {rows_written} rows written")
    return rows_written


def update_option_chain(
    client: NSEMarketClient,
    underlyings: list[tuple[str, bool]],
    start_date: date,
    end_date: date,
    out_dir: Path,
    resume: bool,
    log_every: int,
) -> int:
    logger = RunLogger(out_dir)
    progress_path = out_dir / "market-options-progress.json"

    active_underlyings: list[tuple[str, bool, date]] = []
    for symbol, is_index in underlyings:
        effective_start = start_date
        if resume:
            last_saved = read_last_saved_date(
                option_output_path(out_dir, symbol), "date"
            )
            if last_saved is not None:
                candidate_start = last_saved + timedelta(days=1)
                if candidate_start > effective_start:
                    effective_start = candidate_start
                logger.info(
                    f"{symbol}: option snapshots resume after {last_saved.isoformat()}"
                )
        if effective_start <= end_date:
            active_underlyings.append((symbol, is_index, effective_start))
        else:
            logger.info(f"{symbol}: option snapshots already up to date")

    if not active_underlyings:
        payload = {
            "status": "completed",
            "started_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "requested_start_date": start_date.isoformat(),
            "requested_end_date": end_date.isoformat(),
            "underlyings": [symbol for symbol, _ in underlyings],
            "rows_written": 0,
            "days_total": 0,
            "units_total": 0,
            "units_processed": 0,
            "message": "All option snapshots were already up to date.",
        }
        write_progress(progress_path, payload)
        logger.info("All option snapshots are already up to date")
        return 0

    processing_days = list(
        iter_weekdays(
            min(effective_start for _, _, effective_start in active_underlyings),
            end_date,
        )
    )
    total_units = sum(
        1
        for day in processing_days
        for _, _, effective_start in active_underlyings
        if effective_start <= day
    )

    progress = {
        "status": "running",
        "started_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "requested_start_date": start_date.isoformat(),
        "requested_end_date": end_date.isoformat(),
        "days_total": len(processing_days),
        "units_total": total_units,
        "units_processed": 0,
        "last_processed_day": None,
        "underlyings": [symbol for symbol, _ in underlyings],
        "active_underlyings": [symbol for symbol, _, _ in active_underlyings],
        "underlying_start_dates": {
            symbol: effective_start.isoformat()
            for symbol, _, effective_start in active_underlyings
        },
        "rows_written": 0,
        "symbol_rows_written": {symbol: 0 for symbol, _, _ in active_underlyings},
    }
    write_progress(progress_path, progress)

    rows_written = 0
    processed_units = 0
    for day_index, snapshot_date in enumerate(processing_days, start=1):
        current_underlyings = [
            (symbol, is_index)
            for symbol, is_index, effective_start in active_underlyings
            if effective_start <= snapshot_date
        ]
        for symbol, is_index in current_underlyings:
            status_id = day_status_id(symbol, snapshot_date)
            if resume:
                cached_status = load_cached_status("option-chain", status_id)
                if cached_status and cached_status.get("status") == "downloaded":
                    processed_units += 1
                    progress["units_processed"] = processed_units
                    progress["last_processed_day"] = snapshot_date.isoformat()
                    progress["updated_at"] = datetime.now().isoformat()
                    continue

            cache_path = option_cache_path(symbol, snapshot_date)
            payload = client.fetch_option_chain(symbol, is_index)
            cache_payload(cache_path, payload)
            
            if not payload:
                logger.info(
                    f"{symbol}: empty payload received (may indicate API failure or no options available)"
                )
            
            rows = summarize_option_chain(payload, symbol, is_index, snapshot_date)
            added = merge_rows(
                option_output_path(out_dir, symbol),
                OPTION_FIELDS,
                rows,
                ("symbol", "date", "expiry"),
                ("date", "expiry", "symbol"),
            )
            write_cached_status(
                "option-chain",
                status_id,
                {
                    "status": "downloaded",
                    "symbol": symbol,
                    "snapshot_date": snapshot_date.isoformat(),
                    "is_index": is_index,
                    "cache_path": str(cache_path),
                    "is_empty_payload": not bool(payload),
                    "rows_added": added,
                    "checked_at": datetime.now().isoformat(),
                },
            )
            progress["rows_written"] += added
            progress["symbol_rows_written"][symbol] += added
            rows_written += added
            processed_units += 1
            progress["units_processed"] = processed_units
            progress["last_processed_day"] = snapshot_date.isoformat()
            progress["updated_at"] = datetime.now().isoformat()

        write_progress(progress_path, progress)
        if (
            day_index == 1
            or day_index == len(processing_days)
            or day_index % log_every == 0
        ):
            logger.info(
                f"Option chain: processed {day_index}/{len(processing_days)} trading days through {snapshot_date.isoformat()}"
            )

    progress["status"] = "completed"
    progress["updated_at"] = datetime.now().isoformat()
    write_progress(progress_path, progress)
    logger.info(f"Finished option-chain update with {rows_written} rows written")
    return rows_written


def render_table(rows: list[dict], columns: list[tuple[str, str]]) -> str:
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


def write_domain_output(
    rows: list[dict], fieldnames: list[str], output_format: str, output_path: str | None
) -> None:
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
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
        else:
            writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return

    columns = [
        (fieldname, fieldname.upper())
        for fieldname in fieldnames[: min(len(fieldnames), 7)]
    ]
    content = render_table(rows, columns)
    if output_path:
        Path(output_path).write_text(content + "\n", encoding="utf-8")
    else:
        print(content)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pull NSE option-chain, corporate-action, and announcement data for the stock list."
    )
    parser.add_argument(
        "symbols",
        nargs="*",
        help="NSE symbols like SBIN RELIANCE TCS or a comma-separated list",
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
        "--domain",
        choices=("all", "announcements", "actions", "options"),
        default="all",
        help="Feature domain to pull (default: all)",
    )
    parser.add_argument(
        "--include-indices",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include major option indices when pulling option-chain snapshots (default: enabled)",
    )
    parser.add_argument(
        "--option-indices",
        default=",".join(DEFAULT_OPTION_INDICES),
        help="Comma-separated option index symbols to snapshot (default: NIFTY,BANKNIFTY,FINNIFTY,NIFTYIT)",
    )
    parser.add_argument(
        "--requests-per-second",
        type=float,
        default=0.0,
        help="Global request throttle across all requests (default: 0 = unlimited)",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json", "csv"),
        default="csv",
        help="Output format for combined mode (default: csv)",
    )
    parser.add_argument(
        "--output",
        help="Optional combined output file path. Requires a single --domain selection.",
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
        default=25,
        help="Progress log interval in symbols/underlyings (default: 25)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds (default: 30)",
    )
    return parser


def load_option_indices(value: str) -> list[str]:
    indices: list[str] = []
    seen: set[str] = set()
    for item in value.split(","):
        cleaned = item.strip().upper()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            indices.append(cleaned)
    return indices


def run_combined_mode(
    client: NSEMarketClient, args: argparse.Namespace, symbols: list[str]
) -> int:
    if args.domain == "all":
        raise RuntimeError("--output requires selecting a single --domain")

    if args.domain == "announcements":
        rows: list[dict] = []
        for symbol in symbols:
            rows.extend(
                normalize_announcement_row(row)
                for row in client.fetch_announcements(
                    symbol, args.start_date, args.end_date
                )
            )
        rows = [row for row in rows if row["announcement_date"]]
        rows.sort(
            key=lambda row: (
                row["symbol"],
                row["announcement_date"],
                row["announcement_timestamp"],
                row["sequence_id"],
            )
        )
        if not rows:
            print(
                "No announcement rows found for the requested symbols and date range.",
                file=sys.stderr,
            )
            return 1
        write_domain_output(rows, ANNOUNCEMENT_FIELDS, args.format, args.output)
        return 0

    if args.domain == "actions":
        rows = []
        for symbol in symbols:
            rows.extend(
                normalize_action_row(row)
                for row in client.fetch_actions(symbol, args.start_date, args.end_date)
            )
        rows = [row for row in rows if row["ex_date"]]
        rows.sort(
            key=lambda row: (
                row["symbol"],
                row["ex_date"],
                row["record_date"],
                row["subject"],
            )
        )
        if not rows:
            print(
                "No corporate action rows found for the requested symbols and date range.",
                file=sys.stderr,
            )
            return 1
        write_domain_output(rows, ACTION_FIELDS, args.format, args.output)
        return 0

    rows = []
    for symbol, is_index in iter_option_underlyings(
        symbols, args.include_indices, load_option_indices(args.option_indices)
    ):
        payload = client.fetch_option_chain(symbol, is_index)
        rows.extend(summarize_option_chain(payload, symbol, is_index, args.end_date))
    rows.sort(key=lambda row: (row["symbol"], row["date"], row["expiry"]))
    if not rows:
        print(
            "No option-chain rows found for the requested underlyings.", file=sys.stderr
        )
        return 1
    write_domain_output(rows, OPTION_FIELDS, args.format, args.output)
    return 0


def run() -> int:
    parser = build_parser()
    args = parser.parse_args()

    symbols_file = resolve_symbols_file(args.symbols, args.symbols_file)
    try:
        symbols = load_symbols(args.symbols, symbols_file)
    except OSError as exc:
        print(f"Could not read symbols file: {exc}", file=sys.stderr)
        return 1

    if not symbols:
        parser.error("provide at least one symbol or use --symbols-file")

    if args.start_date > args.end_date:
        parser.error("--start-date must be on or before --end-date")
    if args.requests_per_second < 0:
        parser.error("--requests-per-second must be 0 or greater")
    if args.log_every < 1:
        parser.error("--log-every must be at least 1")

    client = NSEMarketClient(
        timeout=args.timeout, requests_per_second=args.requests_per_second
    )

    if args.output:
        try:
            return run_combined_mode(client, args, symbols)
        except requests.RequestException as exc:
            print(f"Failed to download NSE market feature data: {exc}", file=sys.stderr)
            return 1
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        if args.domain in {"all", "announcements"}:
            update_announcements(
                client,
                symbols,
                args.start_date,
                args.end_date,
                out_dir,
                args.resume,
                args.log_every,
            )
        if args.domain in {"all", "actions"}:
            update_actions(
                client,
                symbols,
                args.start_date,
                args.end_date,
                out_dir,
                args.resume,
                args.log_every,
            )
        if args.domain in {"all", "options"}:
            update_option_chain(
                client,
                iter_option_underlyings(
                    symbols,
                    args.include_indices,
                    load_option_indices(args.option_indices),
                ),
                args.start_date,
                args.end_date,
                out_dir,
                args.resume,
                args.log_every,
            )
    except requests.RequestException as exc:
        print(f"Failed to download NSE market feature data: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(run())
