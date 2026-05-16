#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import requests

from nse_puller import DEFAULT_SYMBOLS_FILE, PROJECT_DIR, load_symbols


KITE_API_BASE_URL = "https://api.kite.trade"
KITE_API_VERSION = "3"
DEFAULT_OUT_DIR = PROJECT_DIR / "out" / "minute-data"
OUTPUT_FIELDS = ["symbol", "timestamp", "open", "high", "low", "close", "volume", "oi"]
INTERVAL_CHOICES = (
    "minute",
    "3minute",
    "5minute",
    "10minute",
    "15minute",
    "30minute",
    "60minute",
    "day",
)


class KiteClient:
    def __init__(self, api_key: str, access_token: str, timeout: int, exchange: str) -> None:
        self.timeout = timeout
        self.exchange = exchange.upper()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-Kite-Version": KITE_API_VERSION,
                "Authorization": f"token {api_key}:{access_token}",
            }
        )

    def load_instruments_csv(self, cache_path: Path, refresh: bool) -> str:
        if cache_path.exists() and not refresh and datetime.fromtimestamp(cache_path.stat().st_mtime).date() == date.today():
            return cache_path.read_text(encoding="utf-8")

        response = self.session.get(
            f"{KITE_API_BASE_URL}/instruments/{self.exchange}",
            timeout=self.timeout,
        )
        response.raise_for_status()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(response.text, encoding="utf-8")
        return response.text

    def resolve_instruments(
        self,
        symbols: list[str],
        cache_path: Path,
        refresh: bool,
    ) -> dict[str, int]:
        csv_text = self.load_instruments_csv(cache_path=cache_path, refresh=refresh)
        reader = csv.DictReader(csv_text.splitlines())
        by_symbol: dict[str, int] = {}

        for row in reader:
            tradingsymbol = (row.get("tradingsymbol") or "").strip().upper()
            if tradingsymbol not in symbols:
                continue
            if (row.get("exchange") or "").strip().upper() != self.exchange:
                continue
            if (row.get("instrument_type") or "").strip().upper() != "EQ":
                continue
            instrument_token = (row.get("instrument_token") or "").strip()
            if not instrument_token:
                continue
            by_symbol[tradingsymbol] = int(instrument_token)

        missing = [symbol for symbol in symbols if symbol not in by_symbol]
        if missing:
            missing_text = ", ".join(missing)
            raise RuntimeError(
                f"Could not resolve Kite instrument tokens for: {missing_text}. "
                f"Check the symbols or pass symbols listed on the {self.exchange} exchange."
            )

        return by_symbol

    def fetch_candles(
        self,
        instrument_token: int,
        interval: str,
        start_at: datetime,
        end_at: datetime,
        include_oi: bool,
    ) -> list[list[object]]:
        candles: list[list[object]] = []
        cursor = start_at

        while cursor <= end_at:
            chunk_end = min(end_at, datetime.combine(cursor.date(), time(23, 59, 59)))
            params = {
                "from": cursor.strftime("%Y-%m-%d %H:%M:%S"),
                "to": chunk_end.strftime("%Y-%m-%d %H:%M:%S"),
                "oi": 1 if include_oi else 0,
            }
            response = self.session.get(
                f"{KITE_API_BASE_URL}/instruments/historical/{instrument_token}/{interval}",
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("status") != "success":
                raise RuntimeError(payload.get("message") or "Kite historical API request failed.")

            candles.extend(payload.get("data", {}).get("candles", []))
            cursor = chunk_end + timedelta(seconds=1)

        return candles


def parse_datetime_value(value: str, *, end_of_day: bool = False) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(value, fmt)
            if fmt == "%Y-%m-%d":
                if end_of_day:
                    return datetime.combine(parsed.date(), time(23, 59, 59))
                return datetime.combine(parsed.date(), time(9, 15, 0))
            return parsed
        except ValueError:
            continue

    raise argparse.ArgumentTypeError(
        f"invalid datetime '{value}', expected YYYY-MM-DD or YYYY-MM-DD HH:MM:SS"
    )


def render_table(rows: list[dict[str, object]]) -> str:
    columns = [
        ("symbol", "SYMBOL"),
        ("timestamp", "TIMESTAMP"),
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


def normalize_candles(symbol: str, candles: list[list[object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for candle in candles:
        row = {
            "symbol": symbol,
            "timestamp": candle[0] if len(candle) > 0 else "",
            "open": candle[1] if len(candle) > 1 else "",
            "high": candle[2] if len(candle) > 2 else "",
            "low": candle[3] if len(candle) > 3 else "",
            "close": candle[4] if len(candle) > 4 else "",
            "volume": candle[5] if len(candle) > 5 else "",
            "oi": candle[6] if len(candle) > 6 else "",
        }
        rows.append(row)
    return rows


def symbol_output_path(out_dir: Path, symbol: str) -> Path:
    return out_dir / f"{symbol}.csv"


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_output(rows: list[dict[str, object]], output_format: str, output_path: Path | None) -> None:
    if output_format == "json":
        content = json.dumps(rows, indent=2)
    elif output_format == "table":
        content = render_table(rows)
    else:
        if output_path is None:
            writer = csv.DictWriter(sys.stdout, fieldnames=OUTPUT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            return
        write_csv(output_path, rows)
        return

    if output_path is None:
        print(content)
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content + "\n", encoding="utf-8")


def load_kite_credentials(args: argparse.Namespace) -> tuple[str, str]:
    api_key = args.api_key or os.environ.get("KITE_API_KEY")
    access_token = args.access_token or os.environ.get("KITE_ACCESS_TOKEN")
    if not api_key or not access_token:
        raise RuntimeError(
            "Kite credentials are required. Set KITE_API_KEY and KITE_ACCESS_TOKEN or pass --api-key and --access-token."
        )
    return api_key, access_token


def default_end_datetime() -> datetime:
    now = datetime.now()
    if now.hour < 9 or (now.hour == 9 and now.minute < 15):
        return datetime.combine(now.date(), time(9, 15, 0))
    return now.replace(microsecond=0)


def parse_end_datetime_value(value: str) -> datetime:
    return parse_datetime_value(value, end_of_day=True)


def instruments_cache_path(exchange: str) -> Path:
    return PROJECT_DIR / ".cache" / "kite" / "instruments" / f"{exchange.upper()}.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pull Kite historical candle data for NSE symbols, including minute intervals."
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
        "--api-key",
        help="Kite Connect API key. Defaults to KITE_API_KEY.",
    )
    parser.add_argument(
        "--access-token",
        help="Kite Connect access token. Defaults to KITE_ACCESS_TOKEN.",
    )
    parser.add_argument(
        "--exchange",
        default="NSE",
        help="Exchange used for symbol resolution (default: NSE)",
    )
    parser.add_argument(
        "--interval",
        choices=INTERVAL_CHOICES,
        default="minute",
        help="Historical candle interval (default: minute)",
    )
    parser.add_argument(
        "--from",
        dest="from_datetime",
        type=parse_datetime_value,
        required=True,
        help="Start timestamp in YYYY-MM-DD or YYYY-MM-DD HH:MM:SS format.",
    )
    parser.add_argument(
        "--to",
        dest="to_datetime",
        type=parse_end_datetime_value,
        default=default_end_datetime(),
        help="End timestamp in YYYY-MM-DD or YYYY-MM-DD HH:MM:SS format. Defaults to now.",
    )
    parser.add_argument(
        "--oi",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Request open interest data when the instrument supports it (default: disabled)",
    )
    parser.add_argument(
        "--refresh-instruments",
        action="store_true",
        help="Refresh the cached Kite instruments CSV before resolving symbols.",
    )
    parser.add_argument(
        "--format",
        choices=("csv", "json", "table"),
        default="csv",
        help="Output format (default: csv)",
    )
    parser.add_argument(
        "--output",
        help="Optional combined output file path. Without this, CSV runs write one file per symbol under out/minute-data/.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT_DIR),
        help="Output directory for per-symbol CSV files (default: ./out/minute-data)",
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

    if args.from_datetime > args.to_datetime:
        parser.error("--from must be on or before --to")

    if args.timeout < 1:
        parser.error("--timeout must be at least 1")

    try:
        api_key, access_token = load_kite_credentials(args)
        client = KiteClient(
            api_key=api_key,
            access_token=access_token,
            timeout=args.timeout,
            exchange=args.exchange,
        )
        instrument_map = client.resolve_instruments(
            symbols=symbols,
            cache_path=instruments_cache_path(args.exchange),
            refresh=args.refresh_instruments,
        )
    except (RuntimeError, requests.RequestException) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    all_rows: list[dict[str, object]] = []
    try:
        for symbol in symbols:
            candles = client.fetch_candles(
                instrument_token=instrument_map[symbol],
                interval=args.interval,
                start_at=args.from_datetime,
                end_at=args.to_datetime,
                include_oi=args.oi,
            )
            rows = normalize_candles(symbol=symbol, candles=candles)
            all_rows.extend(rows)

            if args.output is None and args.format == "csv":
                write_csv(symbol_output_path(Path(args.out_dir), symbol), rows)
    except (RuntimeError, requests.RequestException) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.output is None and args.format == "csv":
        return 0

    write_output(
        rows=all_rows,
        output_format=args.format,
        output_path=Path(args.output) if args.output else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
