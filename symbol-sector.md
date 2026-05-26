# Symbol To Sector Mapping

This is a practical manual mapping for the current watchlist.

Notes:
- `Primary sector index` is the first-choice sector trend to use for that symbol.
- `Secondary proxy` is a fallback or supporting sector/index trend.
- This is a best-effort trading map, not a strict official NSE constituent file.
- For production-grade sector membership, use official NSE index constituent lists.

| Symbol | Primary sector index | Secondary proxy | Notes |
| --- | --- | --- | --- |
| ABB | NIFTY INDIA MFG | Nifty Infra | Industrial electrification, engineering, capex proxy |
| ADANIPORTS | Nifty Infra | Nifty Services Sector | Ports and logistics |
| BEL | Nifty Ind Defence | Nifty PSE | Defence electronics PSU |
| BOSCHLTD | Nifty Auto | NIFTY INDIA MFG | Auto components / industrial tech |
| CUMMINSIND | NIFTY INDIA MFG | Nifty Infra | Engines, power systems, capex proxy |
| DIXON | NIFTY CONSR DURBL | NIFTY INDIA MFG | Electronics manufacturing / consumer durables |
| DLF | Nifty Realty | Nifty Infra | Real estate |
| DMART | Nifty Consumption | Nifty FMCG | Retail consumption proxy |
| ETERNAL | Nifty New Consump | Nifty Services Sector | New-age consumer / platform proxy |
| HAL | Nifty Ind Defence | Nifty PSE | Defence aerospace PSU |
| HINDALCO | Nifty Metal | Nifty Commodities | Metals |
| INDHOTEL | Nifty Ind Tourism | Nifty Services Sector | Hotels / travel |
| INDIGO | Nifty Ind Tourism | Nifty Services Sector | Airlines / travel |
| LENSKART | Nifty New Consump | NIFTY CONSR DURBL | Consumer discretionary / retail proxy |
| LT | Nifty Infra | NIFTY INDIA MFG | Infrastructure and capital goods |
| MANAPPURAM | Nifty Fin Service | Nifty FinSerExBnk | NBFC / financial services |
| MARUTI | Nifty Auto | Nifty Consumption | Passenger vehicles |
| PAYTM | Nifty Fin Service | Nifty New Consump | Fintech / payments |
| PGEL | NIFTY CONSR DURBL | NIFTY INDIA MFG | PG Electroplast style consumer durables / manufacturing proxy |
| SBIN | Nifty Bank | Nifty PSU Bank | Banking |
| SUZLON | Nifty Energy | Nifty India Mfg | Renewables / industrial proxy |
| TRENT | Nifty Consumption | Nifty New Consump | Retail consumption |

## Suggested Default Use

If a symbol has both primary and secondary mappings:
- use `Primary sector index` for the main sector trend
- use `Secondary proxy` as confirmation when needed

## Likely Next Step

Use this file to build `sector index trend` in one of two ways:
1. user passes a stock symbol, and the code looks up its primary sector index here
2. user passes a sector index directly, and the code bypasses this mapping
