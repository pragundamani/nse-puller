# Sources

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
