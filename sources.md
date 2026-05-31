# Sources

## GitHub

| Resource | URL | Notes |
| --- | --- | --- |
| Main repo | `https://github.com/pragundamani/nse-puller` | Primary application repo. |
| Clone URL | `https://github.com/pragundamani/nse-puller.git` | Git remote used by this workspace. |
| Stocks data repo | `https://github.com/pragundamani/stocks` | Nested `out/stocks` history repo. |
| Stocks clone URL | `https://github.com/pragundamani/stocks.git` | Git remote for the nested stocks repo. |

## NSE

| Source | Report Code | Listing/API URL | Direct File Pattern / Example | Notes |
| --- | --- | --- | --- | --- |
| Bhavcopy (PR zip) | `eqbhav` | `https://www.nseindia.com/api/daily-reports?key=CM` | `https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PRDDMMYY.zip` | Daily reports API exposes the exact dated file URL. |
| Market Activity Report | `eqmkt` | `https://www.nseindia.com/api/daily-reports?key=CM` | `https://nsearchives.nseindia.com/archives/equities/mkt/MADDMMYY.csv` | Exchange-level daily market activity report. |
| Security-wise Delivery Positions | `eqmto` | `https://www.nseindia.com/api/daily-reports?key=CM` | `https://nsearchives.nseindia.com/archives/equities/mto/MTO_DDMMYYYY.DAT` | Delivery-position daily file. |
| Daily Volatility | `cmvolt` | `https://www.nseindia.com/api/daily-reports?key=CM` | `https://nsearchives.nseindia.com/archives/nsccl/volt/CMVOLT_DDMMYYYY.CSV` | Daily volatility file from NSE/NSCCL reports. |
| Full Bhavcopy and Security Deliverable Data | `combine` | `https://www.nseindia.com/api/daily-reports?key=CM` | `https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv` | Combined whole-market dataset. |
| Common Bhavcopy Final (zip) | n/a | `https://www.nseindia.com/api/daily-reports?key=CM` | `https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip` | New common bhavcopy zip also exposed in daily reports. |
| Historical Security Price/Volume | n/a | `https://www.nseindia.com/api/historicalOR/priceAndVolumeDataPerSecurity?symbol=...&from=DD-MM-YYYY&to=DD-MM-YYYY` | API response or `&csv=true` | Backing API for `report-detail/eq_security`. |
| Historical India VIX | n/a | `https://www.nseindia.com/api/historicalOR/vixhistory?from=DD-MM-YYYY&to=DD-MM-YYYY` | API response or `&csv=true` | Backing API for `reports-indices-historical-vix`. |

## NSE Market Features / Fundamentals

| Source | Report Code | Listing/API URL | Direct File Pattern / Example | Notes |
| --- | --- | --- | --- | --- |
| Corporate Announcements | n/a | `https://www.nseindia.com/api/corporate-announcements?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY&symbol=<SYMBOL>` | API response | Used by `nse_market_puller.py --domain announcements`. |
| Corporate Actions | n/a | `https://www.nseindia.com/api/corporates-corporateActions?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY&symbol=<SYMBOL>` | API response | Used by `nse_market_puller.py --domain actions`. |
| Option Chain v3 Equity | n/a | `https://www.nseindia.com/api/option-chain-v3?type=Equity&symbol=<SYMBOL>&expiry=latest` | API response | Summarized into `out/option-chain/<SYMBOL>.csv`. |
| Option Chain v3 Index | n/a | `https://www.nseindia.com/api/option-chain-v3?type=Indices&symbol=<INDEX>&expiry=latest` | API response | Used for `NIFTY`, `BANKNIFTY`, `FINNIFTY`, `NIFTYIT` snapshots. |
| Quote Snapshot / Market Cap | n/a | `https://www.nseindia.com/api/quote-equity?symbol=<SYMBOL>` | API response | Mentioned in `plan.md` for market-cap snapshots and metadata. |
| Corporate Financial Results | n/a | `https://www.nseindia.com/api/corporates-financial-results?symbol=<SYMBOL>` | API response | Mentioned in `plan.md` for quarterly financial history. |

## Reference Pages / API Documentation

| Page / Reference | URL | Notes |
| --- | --- | --- |
| Daily reports landing pattern | `https://www.nseindia.com/api/daily-reports?key=CM` | Used to discover direct archive file URLs for bhavcopy, market activity, delivery, and volatility reports. |
| Option chain page | `https://www.nseindia.com/option-chain` | Browser page whose JSON endpoints are used by `option-chain-v3`. |
| Corporate announcements page | `https://www.nseindia.com/companies-listing/corporate-filings-announcements` | Referer/landing page for the announcements API. |
| Corporate actions page | `https://www.nseindia.com/companies-listing/corporate-filings-actions` | Referer/landing page for the corporate actions API. |
| Historical security report page backing API | `https://www.nseindia.com/report-detail/eq_security` | UI page backed by `historicalOR/priceAndVolumeDataPerSecurity`. |
| Historical India VIX report page backing API | `https://www.nseindia.com/reports-indices-historical-vix` | UI page backed by `historicalOR/vixhistory`. |
