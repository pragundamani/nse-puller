# TODOs

- Finish stabilizing `nse_sentiment.py` for full-history runs.
- Add progress logging during long backfills so it is obvious which symbol/date window is currently running.
- Write intermediate output earlier in the run instead of only at the end of a symbol, so partial progress survives timeouts.
- Investigate why full `SBIN` runs are not reaching the first file write.
- Narrow or optimize historical source fan-out further if needed.
- Re-test with a small range like `SBIN --start-date 2026-01-01 --end-date 2026-05-02` and confirm `out/sentiments/SBIN.csv` is created.
- After the short-range test passes, rerun full `SBIN` backfill.
- Confirm `out/sentiments/combined.csv` rebuilds correctly after at least one symbol succeeds.
- Consider adding a local raw-response cache for GDELT/RSS to speed reruns.
- Consider CPU-only Torch install if GPU CUDA packages are unnecessary in this environment.

# Brief

- You asked for a script to provide sentiment analysis for stock data.
- We clarified that you wanted real news/text sentiment, not price-derived sentiment.
- We chose numeric daily sentiment from the oldest trading day to today, with missing days written as `0`.
- We chose weekend/holiday news to roll forward to the next trading day.
- We chose multiple sources to fill gaps: GDELT, Google News RSS, and Bing News RSS.
- We chose per-symbol output at `out/sentiments/<SYMBOL>.csv` and an aggregate `out/sentiments/combined.csv`.
- We added `market_event` and `impact_score` columns.
- I implemented a new CLI in `nse_sentiment.py`, added the `nse-sentiment` entrypoint, and updated dependencies and `README.md`.
- I installed the new dependencies into `.venv`, including `torch` and `transformers`.
- Full-history `SBIN` runs did not complete successfully yet.
- The first full run timed out before writing outputs.
- I reduced historical query fan-out and fixed a FinBERT output-shape bug.
- A later recent-range rerun was manually aborted before completion.
- There are currently no lingering sentiment/backfill processes running.
