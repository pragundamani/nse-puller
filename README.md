# NSE Puller

Fetch daily NSE historical equity data for the stock symbols you choose.

## Quick Start

```bash
git clone https://github.com/pragundamani/nse-puller.git
cd nse-puller
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
nse-stock-picker
nse-puller
bse-puller --help
nse-minute-data --help
```

## Setup

```bash
git clone https://github.com/pragundamani/nse-puller.git
cd nse-puller
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

## Stock Picker

Use the separate interactive picker app to edit `stocks.txt` with a fuzzy checklist UI:

```bash
nse-stock-picker
```

Useful options:

```bash
nse-stock-picker --page-size 30
nse-stock-picker --refresh-symbol-list
nse-stock-picker --stocks-file /tmp/my-stocks.txt
```

How it works:

- It downloads and caches the NSE symbol universe from `EQUITY_L.csv`.
- It fuzzy-matches by symbol and company name.
- It saves the selected symbols back into `stocks.txt` or the file you pass with `--stocks-file`.
- `nse-puller` then uses that `stocks.txt` by default when you run it with no symbols.
- If `stocks.txt` is missing or empty and `nse-puller` is running in a real terminal, it auto-launches the picker for you.

## Usage

Update one CSV per stock under `out/stocks/`:

```bash
nse-puller RELIANCE TCS INFY
```

Update from the project `stocks.txt` file:

```bash
nse-puller
```

Write a combined CSV instead of per-stock files:

```bash
nse-puller --symbols-file stocks.txt --output history.csv
```

Pick a custom date range:

```bash
nse-puller RELIANCE TCS --start-date 2015-01-01 --end-date 2020-12-31 --output history.csv
```

Run more gently against NSE:

```bash
nse-puller --symbols-file stocks.txt --out-dir out --workers 1 --requests-per-second 1.5
```

Write JSON or a small table output:

```bash
nse-puller --symbols-file stocks.txt --format json --output history.json
nse-puller RELIANCE --start-date 2024-01-01 --end-date 2024-01-10 --format table
```

Pull matching BSE history for the shared stock list:

```bash
bse-puller
```

Useful options:

```bash
bse-puller ABB SBIN --start-date 2024-01-01 --end-date 2024-01-31
bse-puller --symbols-file stocks.txt --mappings-file bse-stocks.txt --output bse-history.csv
```

Run the daily updater manually:

```bash
./scripts/daily_update.sh
```

Build incremental sentiment history from old stock dates through the latest trading day:

```bash
nse-sentiment
```

Useful options:

```bash
nse-sentiment SBIN LT
nse-sentiment --symbols-file stocks.txt --refresh-days 5
nse-sentiment --start-date 2024-01-01 --end-date 2024-12-31
```

## Minute Data

Use the separate Kite-backed script for minute and other intraday intervals:

```bash
export KITE_API_KEY=your_api_key
export KITE_ACCESS_TOKEN=your_access_token

nse-minute-data RELIANCE --from "2026-05-15 09:15:00" --to "2026-05-15 15:30:00"
```

Useful options:

```bash
nse-minute-data --symbols-file stocks.txt --from "2026-05-15 09:15:00" --to "2026-05-15 09:30:00"
nse-minute-data RELIANCE --interval 5minute --from 2026-05-01 --to "2026-05-02 15:30:00" --output reliance-5m.csv
nse-minute-data INFY --from "2026-05-15 09:15:00" --to "2026-05-15 09:20:00" --format json --output infy.json
```

Notes:

- `nse-minute-data` uses Zerodha Kite historical candles, not the NSE bhavcopy archive.
- It resolves NSE symbols through Kite's instruments API and caches the NSE instrument list under `.cache/kite/instruments/NSE.csv`.
- Default CSV mode writes one file per symbol under `out/minute-data/`.
- Kite credentials can be passed with `--api-key` and `--access-token`, or through `KITE_API_KEY` and `KITE_ACCESS_TOKEN`.

## BSE Daily Data

Use the separate BSE daily-history script with the same shared stock universe from `stocks.txt`:

```bash
bse-puller
```

Notes:

- `bse-puller` reads shared symbols from `stocks.txt` by default.
- It resolves those shared symbols through `bse-stocks.txt`, which maps each symbol to a BSE scrip code.
- Default CSV mode writes one file per symbol under `out/bse-stocks/`.
- BSE rows are normalized to the same output header order as `nse-puller`.
- BSE daily bhavcopy ZIP files are cached under `.cache/bse-bhavcopy/`.

Install the weekday cron job:

```bash
./scripts/install_cron.sh
```

## Output Layout

```text
out/
  logs/
    latest.log
    run-YYYYMMDD-HHMMSS.log
    cron.log
  progress.json
  bse-progress.json
  sentiments/
    SBIN.csv
    LT.csv
    combined.csv
  bse-stocks/
    ABB.csv
    SBIN.csv
  stocks/
    RELIANCE.csv
    TCS.csv
    INFY.csv
```

## Notes

- Default range is `2008-01-01` through today.
- Default CSV mode writes one file per stock under `out/stocks/` and resumes from the last saved date in each file.
- `bse-puller` writes one file per stock under `out/bse-stocks/` and tracks its run state in `out/bse-progress.json`.
- Output columns include `symbol`, `date`, `open`, `high`, `low`, `close`, `volume`, and related daily fields.
- `nse-sentiment` writes one daily file per stock under `out/sentiments/<SYMBOL>.csv` and rebuilds `out/sentiments/combined.csv` after each run.
- Sentiment rows are numeric and incremental. Missing-news trading days are written as `0`.
- Weekend and holiday news is assigned to the next available trading day.
- Sentiment output columns are `symbol`, `date`, `sentiment_score`, `headline_count`, `source_count`, `market_event`, and `impact_score`.
- Hidden per-symbol article ledgers are also written under `out/sentiments/` so reruns can refresh the recent tail without losing older deduplicated article history.
- `out/progress.json` is updated while a long run is in progress so you can see the last completed trading day.
- `out/logs/latest.log` contains the latest run log, and timestamped logs are kept for older runs.
- The script caches downloaded bhavcopy zip files under `.cache/bhavcopy/` so reruns do not download the same dates again.
- Requests are globally throttled by default to reduce the chance of rate limiting, and transient `403`/`429`/`5xx` responses are retried with backoff.
- Some old or renamed stocks may not have continuous history under a single symbol.
- Historical news coverage is sourced from GDELT plus recent Google News RSS and Bing News RSS fills, then scored with FinBERT.
