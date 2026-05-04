#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from urllib.parse import quote_plus, urlparse, urlunparse

import requests

from nse_puller import (
    DEFAULT_HEADERS,
    DEFAULT_OUT_DIR,
    DEFAULT_SYMBOLS_FILE,
    load_symbols,
    parse_date,
    resolve_symbols_file,
)
from nse_stock_picker import load_symbol_list_csv


SENTIMENT_FIELDS = [
    "symbol",
    "date",
    "sentiment_score",
    "headline_count",
    "source_count",
    "market_event",
    "impact_score",
]
ARTICLE_FIELDS = [
    "article_id",
    "symbol",
    "assigned_date",
    "published_at",
    "source",
    "title",
    "url",
    "score",
    "market_event",
    "impact_score",
]
FINBERT_MODEL = "ProsusAI/finbert"
GDELT_MAX_RECORDS = 250
DEFAULT_REFRESH_DAYS = 5
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_TIMEOUT = 30
DEFAULT_GDELT_WINDOW_DAYS = 90
MARKET_EVENT_PATTERNS = {
    "war": [
        r"\bwar\b",
        r"\bmilitary\b",
        r"\bmissile\b",
        r"\bairstrike\b",
        r"\bdrone strike\b",
        r"\binvasion\b",
    ],
    "tension": [
        r"\btension(?:s)?\b",
        r"\bconflict\b",
        r"\bstandoff\b",
        r"\bclash(?:es)?\b",
        r"\bborder\b",
        r"\bgeopolitical\b",
        r"\bsanction(?:s)?\b",
        r"\bretaliat(?:e|ion)\b",
    ],
    "commodity_shock": [
        r"\boil spike\b",
        r"\bcrude spike\b",
        r"\bgas prices?\b",
        r"\bsupply disruption\b",
        r"\bshipping disruption\b",
        r"\bexport ban\b",
    ],
    "policy_shock": [
        r"\brate hike\b",
        r"\bemergency rate\b",
        r"\bpolicy shock\b",
        r"\bcentral bank\b",
        r"\btrade war\b",
        r"\btariff\b",
    ],
}


@dataclass(frozen=True)
class TradingRow:
    symbol: str
    trading_date: date


@dataclass(frozen=True)
class Article:
    article_id: str
    symbol: str
    published_at: datetime
    title: str
    source: str
    url: str
    query_term: str


def _safe_isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value.strip())
    if not parsed.scheme:
        return value.strip()
    cleaned = parsed._replace(fragment="", query=parsed.query)
    return urlunparse(cleaned)


def _normalize_text(value: str) -> str:
    text = unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _make_article_id(symbol: str, title: str, source: str, published_at: datetime, url: str) -> str:
    digest = hashlib.sha1(
        "|".join(
            [
                symbol.upper(),
                _normalize_text(title).lower(),
                source.lower(),
                published_at.astimezone(timezone.utc).isoformat(),
                _normalize_url(url),
            ]
        ).encode("utf-8")
    ).hexdigest()
    return digest


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass

    candidates = [
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y%m%dT%H%M%SZ",
        "%Y-%m-%d",
    ]
    for fmt in candidates:
        try:
            parsed = datetime.strptime(value, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _format_score(value: float) -> str:
    rounded = round(value, 6)
    if rounded == 0:
        rounded = 0.0
    return f"{rounded:.6f}".rstrip("0").rstrip(".") if rounded else "0"


def _canonical_event_label(labels: set[str]) -> str:
    if not labels:
        return ""
    return ",".join(sorted(labels))


def detect_market_event(title: str) -> str:
    lowered = _normalize_text(title).lower()
    matched = set()
    for label, patterns in MARKET_EVENT_PATTERNS.items():
        if any(re.search(pattern, lowered) for pattern in patterns):
            matched.add(label)
    return _canonical_event_label(matched)


class FinBertScorer:
    def __init__(self, model_name: str) -> None:
        try:
            from transformers import pipeline
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Sentiment scoring requires 'transformers' and a model backend such as 'torch'. "
                "Run '. .venv/bin/activate' and install the project dependencies again."
            ) from exc

        self.classifier = pipeline(
            "text-classification",
            model=model_name,
            tokenizer=model_name,
            return_all_scores=True,
        )

    def score_many(self, titles: list[str]) -> list[tuple[float, float]]:
        if not titles:
            return []
        raw_results = self.classifier(titles, truncation=True, batch_size=16)
        if titles and isinstance(raw_results, list) and raw_results and isinstance(raw_results[0], dict):
            raw_results = [[item] for item in raw_results]
        scored: list[tuple[float, float]] = []
        for result in raw_results:
            if isinstance(result, dict):
                result = [result]
            probabilities = {item["label"].lower(): float(item["score"]) for item in result}
            positive = probabilities.get("positive", 0.0)
            negative = probabilities.get("negative", 0.0)
            score = positive - negative
            impact = abs(score)
            scored.append((score, impact))
        return scored


class NewsClient:
    def __init__(self, timeout: int, pause_seconds: float = 0.2) -> None:
        self.timeout = timeout
        self.pause_seconds = pause_seconds
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def get_text(self, url: str) -> str:
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        time.sleep(self.pause_seconds)
        return response.text

    def fetch_gdelt_articles(
        self,
        symbol: str,
        query_term: str,
        start_date: date,
        end_date: date,
        max_records: int,
    ) -> list[Article]:
        start_token = f"{start_date:%Y%m%d}000000"
        end_token = f"{(end_date + timedelta(days=1)):%Y%m%d}000000"
        query = quote_plus(f'"{query_term}" sourcecountry:IN')
        url = (
            "https://api.gdeltproject.org/api/v2/doc/doc"
            f"?query={query}&mode=ArtList&format=json&maxrecords={max_records}"
            f"&startdatetime={start_token}&enddatetime={end_token}"
        )
        payload = json.loads(self.get_text(url))
        articles: list[Article] = []
        for item in payload.get("articles", []):
            published_at = _parse_datetime(item.get("seendate", ""))
            title = _normalize_text(item.get("title", ""))
            url_value = _normalize_url(item.get("url", ""))
            source = item.get("domain", "") or "gdelt"
            if not published_at or not title or not url_value:
                continue
            articles.append(
                Article(
                    article_id=_make_article_id(symbol, title, source, published_at, url_value),
                    symbol=symbol,
                    published_at=published_at,
                    title=title,
                    source=source,
                    url=url_value,
                    query_term=query_term,
                )
            )
        return articles

    def fetch_google_news_articles(self, symbol: str, query_term: str, lookback_days: int) -> list[Article]:
        query = quote_plus(f'{query_term} when:{lookback_days}d')
        url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
        return self._parse_rss_articles(symbol, query_term, self.get_text(url), default_source="google-news")

    def fetch_bing_news_articles(self, symbol: str, query_term: str) -> list[Article]:
        query = quote_plus(query_term)
        url = f"https://www.bing.com/news/search?q={query}&format=rss"
        return self._parse_rss_articles(symbol, query_term, self.get_text(url), default_source="bing-news")

    def _parse_rss_articles(
        self,
        symbol: str,
        query_term: str,
        payload: str,
        default_source: str,
    ) -> list[Article]:
        root = ET.fromstring(payload)
        articles: list[Article] = []
        for item in root.findall("./channel/item"):
            title = _normalize_text(item.findtext("title", default=""))
            link = _normalize_url(item.findtext("link", default=""))
            pub_date = _parse_datetime(item.findtext("pubDate", default=""))
            source_text = item.findtext("source", default="") or item.findtext(
                "{https://www.bing.com/news/search?q=SBIN&format=rss}Source",
                default="",
            )
            source = _normalize_text(source_text) or default_source
            if not title or not link or pub_date is None:
                continue
            articles.append(
                Article(
                    article_id=_make_article_id(symbol, title, source, pub_date, link),
                    symbol=symbol,
                    published_at=pub_date,
                    title=title,
                    source=source,
                    url=link,
                    query_term=query_term,
                )
            )
        return articles


def load_symbol_name_map(timeout: int, refresh: bool) -> dict[str, str]:
    try:
        csv_text = load_symbol_list_csv(refresh=refresh, timeout=timeout)
    except RuntimeError:
        return {}

    reader = csv.DictReader(csv_text.splitlines())
    names: dict[str, str] = {}
    for row in reader:
        normalized = {key.strip(): value.strip() for key, value in row.items() if key is not None}
        symbol = normalized.get("SYMBOL", "").upper()
        series = normalized.get("SERIES", "")
        if not symbol or series != "EQ":
            continue
        names[symbol] = normalized.get("NAME OF COMPANY", "")
    return names


def sentiment_output_path(out_dir: Path, symbol: str) -> Path:
    return out_dir / "sentiments" / f"{symbol}.csv"


def article_output_path(out_dir: Path, symbol: str) -> Path:
    return out_dir / "sentiments" / f".{symbol}.articles.csv"


def combined_output_path(out_dir: Path) -> Path:
    return out_dir / "sentiments" / "combined.csv"


def load_trading_rows(csv_path: Path) -> list[TradingRow]:
    rows: list[TradingRow] = []
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            trading_date = row.get("date", "")
            symbol = row.get("symbol", "")
            if not trading_date or not symbol:
                continue
            rows.append(TradingRow(symbol=symbol.upper(), trading_date=date.fromisoformat(trading_date)))
    return rows


def load_existing_sentiment_rows(csv_path: Path) -> dict[date, dict[str, str]]:
    if not csv_path.exists():
        return {}
    existing: dict[date, dict[str, str]] = {}
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw_date = row.get("date", "")
            if not raw_date:
                continue
            existing[date.fromisoformat(raw_date)] = row
    return existing


def latest_existing_date(csv_path: Path) -> date | None:
    if not csv_path.exists():
        return None
    latest: date | None = None
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw_date = row.get("date", "")
            if not raw_date:
                continue
            current = date.fromisoformat(raw_date)
            if latest is None or current > latest:
                latest = current
    return latest


def load_existing_articles(csv_path: Path) -> dict[str, dict[str, str]]:
    if not csv_path.exists():
        return {}
    existing: dict[str, dict[str, str]] = {}
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            article_id = row.get("article_id", "")
            if article_id:
                existing[article_id] = row
    return existing


def build_query_terms(symbol: str, company_name: str) -> list[str]:
    candidates = [symbol, f"{symbol} NSE"]
    if company_name:
        candidates.append(company_name)
        candidates.append(f"{company_name} {symbol}")
    unique: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        normalized = item.strip()
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower())
            unique.append(normalized)
    return unique


def floor_to_month(day: date) -> date:
    return day.replace(day=1)


def next_month(day: date) -> date:
    if day.month == 12:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)


def iter_window_ranges(start_date: date, end_date: date, window_days: int):
    current = start_date
    step = timedelta(days=window_days - 1)
    while current <= end_date:
        window_end = min(current + step, end_date)
        yield current, window_end
        current = window_end + timedelta(days=1)


def assign_to_trading_day(article_day: date, trading_dates: list[date]) -> date | None:
    for trading_day in trading_dates:
        if trading_day >= article_day:
            return trading_day
    return None


def fetch_articles_for_symbol(
    client: NewsClient,
    symbol: str,
    company_name: str,
    start_date: date,
    end_date: date,
    include_google: bool,
    include_bing: bool,
) -> list[Article]:
    articles: dict[str, Article] = {}
    query_terms = build_query_terms(symbol, company_name)
    gdelt_terms = query_terms[:2]
    if company_name:
        gdelt_terms = [company_name, symbol]
    gdelt_terms = list(dict.fromkeys(item for item in gdelt_terms if item))

    for query_term in gdelt_terms:
        for window_start, window_end in iter_window_ranges(start_date, end_date, DEFAULT_GDELT_WINDOW_DAYS):
            try:
                gdelt_articles = client.fetch_gdelt_articles(
                    symbol=symbol,
                    query_term=query_term,
                    start_date=window_start,
                    end_date=window_end,
                    max_records=GDELT_MAX_RECORDS,
                )
            except (requests.RequestException, json.JSONDecodeError, ET.ParseError):
                continue
            for article in gdelt_articles:
                articles.setdefault(article.article_id, article)

    recent_threshold = date.today() - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    if end_date >= recent_threshold:
        recent_query_terms = query_terms[:2]
        if company_name:
            recent_query_terms = [company_name, symbol]
        recent_query_terms = list(dict.fromkeys(item for item in recent_query_terms if item))
        for query_term in recent_query_terms:
            if include_google:
                try:
                    google_articles = client.fetch_google_news_articles(symbol, query_term, DEFAULT_LOOKBACK_DAYS)
                except (requests.RequestException, ET.ParseError):
                    google_articles = []
                for article in google_articles:
                    article_day = article.published_at.date()
                    if start_date <= article_day <= end_date:
                        articles.setdefault(article.article_id, article)
            if include_bing:
                try:
                    bing_articles = client.fetch_bing_news_articles(symbol, query_term)
                except (requests.RequestException, ET.ParseError):
                    bing_articles = []
                for article in bing_articles:
                    article_day = article.published_at.date()
                    if start_date <= article_day <= end_date:
                        articles.setdefault(article.article_id, article)

    return sorted(articles.values(), key=lambda item: item.published_at)


def compute_sentiment_rows(
    symbol: str,
    trading_dates: list[date],
    articles: list[Article],
    scorer: FinBertScorer,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    by_day_scores: dict[date, list[float]] = defaultdict(list)
    by_day_sources: dict[date, set[str]] = defaultdict(set)
    by_day_events: dict[date, set[str]] = defaultdict(set)
    by_day_event_impacts: dict[date, list[float]] = defaultdict(list)
    article_rows: list[dict[str, str]] = []

    scores = scorer.score_many([article.title for article in articles])
    for article, (score, impact) in zip(articles, scores):
        assigned_day = assign_to_trading_day(article.published_at.date(), trading_dates)
        if assigned_day is None:
            continue
        market_event = detect_market_event(article.title)
        by_day_scores[assigned_day].append(score)
        by_day_sources[assigned_day].add(article.source)
        if market_event:
            by_day_events[assigned_day].update(market_event.split(","))
            by_day_event_impacts[assigned_day].append(impact)
        article_rows.append(
            {
                "article_id": article.article_id,
                "symbol": symbol,
                "assigned_date": assigned_day.isoformat(),
                "published_at": _safe_isoformat(article.published_at),
                "source": article.source,
                "title": article.title,
                "url": article.url,
                "score": _format_score(score),
                "market_event": market_event,
                "impact_score": _format_score(impact if market_event else 0.0),
            }
        )

    daily_rows: list[dict[str, str]] = []
    for trading_day in trading_dates:
        scores_for_day = by_day_scores.get(trading_day, [])
        day_score = sum(scores_for_day) / len(scores_for_day) if scores_for_day else 0.0
        impacts = by_day_event_impacts.get(trading_day, [])
        impact_score = sum(impacts) / len(impacts) if impacts else 0.0
        daily_rows.append(
            {
                "symbol": symbol,
                "date": trading_day.isoformat(),
                "sentiment_score": _format_score(day_score),
                "headline_count": str(len(scores_for_day)),
                "source_count": str(len(by_day_sources.get(trading_day, set()))),
                "market_event": _canonical_event_label(by_day_events.get(trading_day, set())),
                "impact_score": _format_score(impact_score),
            }
        )

    return daily_rows, article_rows


def merge_sentiment_rows(
    existing_rows: dict[date, dict[str, str]],
    rebuilt_rows: list[dict[str, str]],
    rewrite_start: date,
) -> list[dict[str, str]]:
    merged = {
        trading_day: row
        for trading_day, row in existing_rows.items()
        if trading_day < rewrite_start
    }
    for row in rebuilt_rows:
        merged[date.fromisoformat(row["date"])] = row
    return [merged[day] for day in sorted(merged)]


def merge_article_rows(
    existing_rows: dict[str, dict[str, str]],
    rebuilt_rows: list[dict[str, str]],
    rewrite_start: date,
) -> list[dict[str, str]]:
    kept = {
        article_id: row
        for article_id, row in existing_rows.items()
        if date.fromisoformat(row["assigned_date"]) < rewrite_start
    }
    for row in rebuilt_rows:
        kept[row["article_id"]] = row
    return [kept[key] for key in sorted(kept, key=lambda item: (kept[item]["assigned_date"], item))]


def write_csv(csv_path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def rebuild_combined_csv(out_dir: Path) -> None:
    combined_rows: list[dict[str, str]] = []
    sentiments_dir = out_dir / "sentiments"
    if not sentiments_dir.exists():
        return
    for csv_path in sorted(sentiments_dir.glob("*.csv")):
        if csv_path.name == "combined.csv":
            continue
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            combined_rows.extend(reader)
    combined_rows.sort(key=lambda row: (row["date"], row["symbol"]))
    write_csv(combined_output_path(out_dir), SENTIMENT_FIELDS, combined_rows)


def stock_csv_path(out_dir: Path, symbol: str) -> Path:
    return out_dir / "stocks" / f"{symbol}.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build incremental daily sentiment series for NSE stocks from historical news."
    )
    parser.add_argument(
        "symbols",
        nargs="*",
        help="NSE symbols like SBIN LT or a comma-separated list",
    )
    parser.add_argument(
        "--symbols-file",
        help="Text file with symbols, one per line or comma-separated. Defaults to stocks.txt in the project directory.",
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_OUT_DIR),
        help="Project-local output directory for stocks and sentiments (default: ./out)",
    )
    parser.add_argument(
        "--start-date",
        type=parse_date,
        help="Optional lower bound in YYYY-MM-DD format. Defaults to the first trading day in each stock CSV.",
    )
    parser.add_argument(
        "--end-date",
        type=parse_date,
        default=date.today(),
        help="Optional upper bound in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--refresh-days",
        type=int,
        default=DEFAULT_REFRESH_DAYS,
        help="Recompute this many trailing trading days on each run to capture late news (default: 5)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="HTTP timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--refresh-symbol-list",
        action="store_true",
        help="Refresh the cached NSE symbol universe before resolving company names",
    )
    parser.add_argument(
        "--skip-google",
        action="store_true",
        help="Skip Google News RSS and rely on GDELT and Bing",
    )
    parser.add_argument(
        "--skip-bing",
        action="store_true",
        help="Skip Bing News RSS and rely on GDELT and Google",
    )
    return parser


def run() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.refresh_days < 0:
        parser.error("--refresh-days must be 0 or greater")
    if args.start_date and args.start_date > args.end_date:
        parser.error("--start-date must be on or before --end-date")

    symbols_file = resolve_symbols_file(args.symbols, args.symbols_file)
    try:
        symbols = load_symbols(args.symbols, symbols_file)
    except OSError as exc:
        print(f"Could not read symbols file: {exc}", file=sys.stderr)
        return 1

    if not symbols:
        parser.error("provide at least one symbol or use --symbols-file")

    out_dir = Path(args.out_dir)
    symbol_names = load_symbol_name_map(timeout=args.timeout, refresh=args.refresh_symbol_list)
    news_client = NewsClient(timeout=args.timeout)
    try:
        scorer = FinBertScorer(FINBERT_MODEL)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    processed_symbols: list[str] = []
    for symbol in symbols:
        stock_path = stock_csv_path(out_dir, symbol)
        if not stock_path.exists():
            print(f"Skipping {symbol}: missing stock history at {stock_path}", file=sys.stderr)
            continue

        trading_rows = load_trading_rows(stock_path)
        if not trading_rows:
            print(f"Skipping {symbol}: no trading rows found in {stock_path}", file=sys.stderr)
            continue

        trading_dates = [row.trading_date for row in trading_rows]
        symbol_start = trading_dates[0]
        symbol_end = min(trading_dates[-1], args.end_date)
        if args.start_date is not None and args.start_date > symbol_start:
            symbol_start = args.start_date
        trading_dates = [day for day in trading_dates if symbol_start <= day <= symbol_end]
        if not trading_dates:
            print(f"Skipping {symbol}: no trading dates fall within the requested range", file=sys.stderr)
            continue

        sentiment_path = sentiment_output_path(out_dir, symbol)
        article_path = article_output_path(out_dir, symbol)
        existing_sentiment = load_existing_sentiment_rows(sentiment_path)
        existing_articles = load_existing_articles(article_path)
        last_existing = latest_existing_date(sentiment_path)
        rewrite_start = trading_dates[0]
        if last_existing is not None:
            recent_dates = [day for day in trading_dates if day <= last_existing]
            if recent_dates:
                if args.refresh_days == 0:
                    rewrite_start = last_existing + timedelta(days=1)
                else:
                    refresh_index = max(0, len(recent_dates) - args.refresh_days)
                    rewrite_start = recent_dates[refresh_index]
            else:
                rewrite_start = trading_dates[0]

        effective_dates = [day for day in trading_dates if day >= rewrite_start]
        if not effective_dates:
            processed_symbols.append(symbol)
            print(f"{symbol}: already up to date", file=sys.stderr)
            continue
        company_name = symbol_names.get(symbol, "")
        articles = fetch_articles_for_symbol(
            client=news_client,
            symbol=symbol,
            company_name=company_name,
            start_date=effective_dates[0],
            end_date=effective_dates[-1],
            include_google=not args.skip_google,
            include_bing=not args.skip_bing,
        )
        rebuilt_daily_rows, rebuilt_article_rows = compute_sentiment_rows(
            symbol=symbol,
            trading_dates=effective_dates,
            articles=articles,
            scorer=scorer,
        )
        merged_daily_rows = merge_sentiment_rows(existing_sentiment, rebuilt_daily_rows, rewrite_start)
        merged_article_rows = merge_article_rows(existing_articles, rebuilt_article_rows, rewrite_start)
        write_csv(sentiment_path, SENTIMENT_FIELDS, merged_daily_rows)
        write_csv(article_path, ARTICLE_FIELDS, merged_article_rows)
        processed_symbols.append(symbol)
        print(
            f"{symbol}: wrote {len(merged_daily_rows)} sentiment rows and {len(merged_article_rows)} article rows",
            file=sys.stderr,
        )

    if not processed_symbols:
        print("No symbol sentiment files were written.", file=sys.stderr)
        return 1

    rebuild_combined_csv(out_dir)
    print(
        f"Wrote sentiment files to {out_dir / 'sentiments'} and rebuilt {combined_output_path(out_dir)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
