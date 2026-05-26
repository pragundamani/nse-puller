# Plan: Fundamentals + LSTM growth-pattern model

## Goal
Build a training-ready dataset + model that:
1) enriches daily stock series with **base fundamentals** (market cap + quarterly financials),
2) computes rolling indicators for multiple lookbacks,
3) trains a **multi-horizon LSTM** to predict forward returns for {1, 2, 3, 5, 7, 10, 15, 21, 30, 42, 63, 84, 126} trading days,
4) surfaces **growth patterns** (clusters) ranked by a **weighted score across all horizons**.

## Current repo/data state
- Python >= 3.10, dependencies include **torch**.
- Existing data outputs under `out/`:
  - `out/stocks/<SYMBOL>.csv` (canonical per-symbol daily history; join key `(symbol,date)`)
  - `out/bhavcopy/YYYY/MM/YYYY-MM-DD.csv` (daily, filtered to `stocks.txt`)
  - `out/delivery-positions/YYYY/MM/YYYY-MM-DD.csv` (daily, filtered)
  - `out/daily-volatility/YYYY/MM/YYYY-MM-DD.csv` (daily, filtered)
  - `out/full-bhavcopy-deliverable/YYYY/MM/YYYY-MM-DD.csv` (daily, filtered)
  - `out/market-activity/YYYY/MM/MAddmmyy.csv` (date-level market report)

## Phase 0: Base fundamentals / market indicators (your new requirement)
### 0.1 What we want per stock
- **Market cap** (ideally daily history for training): marketCap, plus any available metadata to derive market cap.
- **Quarterly results**: revenue, operating profit/EBITDA, net profit, EPS.
- (Optional v1.1) balance sheet + cashflow items + derived ratios (TTM, QoQ/YoY growth).

### 0.2 Sources & docs reality (pragmatic)
- NSE/BSE have official” partner/member API portals
- For automation in this repo, the reliable path is typically the same JSON endpoints used by the public websites.
  - NSE endpoints commonly used:
    - Quote snapshot (market cap): `https://www.nseindia.com/api/quote-equity?symbol=<SYMBOL>`
    - Corporate financial results: `https://www.nseindia.com/api/corporates-financial-results?symbol=<SYMBOL>`
  - Expect anti-bot protections (403 without cookies/headers). Plan assumes session priming + throttling + caching.

### 0.3 Backfill strategy (as far back as possible)
- **Quarterly financials**: pull full available history per symbol from the financial-results endpoint, normalize, store.
- **Historical market cap** (harder):
  - Option A (preferred if feasible): find an endpoint/dataset that returns market cap by date.
  - Option B: reconstruct market cap from `close * shares_outstanding` if shares outstanding is available and we accept approximations between corporate actions.
  - Option C: snapshot daily going forward (fallback if backfill not feasible).

### 0.4 Storage layout
- Raw:
  - `out/fundamentals/quote/latest/<SYMBOL>.json`
  - `out/fundamentals/quote/history/<SYMBOL>/<YYYY-MM-DD>.json` (if we can backfill or reconstruct)
- Normalized:
  - `out/fundamentals/financial-results/<SYMBOL>.csv` (one row per quarter)
  - `out/fundamentals/combined/financial-results.csv` (optional)

### 0.5 Joining fundamentals to daily ML rows
- Join key: `symbol`.
- Forward-fill quarterly values onto each trading day: for date t attach last quarter with `period_end <= t`.

## Phase 1: Consolidate into a training table
Build a merged dataset with 1 row per `(symbol,date)` combining:
- price/volume (from `out/stocks/<SYMBOL>.csv`)
- delivery% (from `out/delivery-positions/...`)
- volatility metrics (from `out/daily-volatility/...`)
- market indicators by date (from `out/market-activity/...`)
- fundamentals forward-filled (Phase 0)

Outputs:
- `out/ml/merged/<SYMBOL>.csv` or `out/ml/merged.csv.gz`

## Phase 2: Feature engineering (requested lookbacks)
Use trading-day windows (rows), not calendar days:
- {1, 2, 3, 5, 7, 10, 15, 21, 30, 42, 63, 84, 126}

Features:
- rolling returns, log returns, volatility
- volume/liquidity regimes
- delivery trends
- market index returns/volatility
- fundamentals: margins, growth, TTM, size factor (market cap)

## Phase 3: Model training (PyTorch LSTM)
- Input: last 126 trading days of feature vectors.
- Output: n-dim vector of forward returns for horizons {1, 2, 3, 5, 7, 10, 15, 21, 30, 42, 63, 84, 126}.
- Loss: weighted regression across horizons (weights configurable).
- Splits: strictly chronological train/val/test to avoid leakage.

Artifacts:
- `out/ml/models/<run_id>/checkpoint.pt`
- `out/ml/models/<run_id>/metrics.json`

## Phase 4: Pattern mining + ranking
- Extract embeddings (e.g., last hidden state) per sample.
- Cluster embeddings into K patterns.
- Rank patterns by realized **weighted forward-return score**.

Outputs:
- `out/ml/patterns/pattern_summary.csv`
- `out/ml/patterns/latest_recos.csv`

## Risks / mitigations
- NSE endpoints may 403: mitigate with session priming, headers, throttling, caching.
- Market cap historical backfill may be incomplete: implement reconstruction/snapshot fallback.

## Execution style
Notebook-first (experimentation), then convert stable pieces into scripts/CLI if desired.
