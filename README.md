# FamilyFund

A local, CSV-based family fund management system using NAV (Net Asset Value) methodology. Track all household assets as a virtual fund — just like a mutual fund, but for your family.

## Why

- **No SaaS dependency** — your financial data stays on your local machine
- **CSV as single source of truth** — human-readable, portable, version-controllable
- **Real fund accounting** — external cash flows change share count, not NAV, so performance measurement is accurate regardless of deposit/withdrawal timing

## Features

- **Fund-level NAV tracking** — single net asset value for your entire portfolio
- **Per-class NAV** — independent performance tracking across 7 asset classes (US/CN index funds, ETF & stocks, fixed income, gold, company stock, cash)
- **Cost basis & P/L** — per-asset profit/loss based on cumulative cash flows
- **Multi-currency** — Exchange_Rate column converts everything to CNY; live FX rate fetch from frankfurter.app
- **Streamlit dashboard** — interactive charts (Plotly), allocation pie, performance table, holdings detail
- **Weekly Update tab** — add new snapshots via UI instead of editing CSV manually
- **History Management** — edit or delete past snapshots
- **Docker + iCloud** — containerized deployment, CSV synced across devices via iCloud Drive

## Quick Start

### Local (without Docker)

```bash
pip install -r requirements.txt
streamlit run dashboard/app.py
```

### Docker

```bash
# Build and run (uses local ./data/ directory)
docker compose up --build

# With iCloud data
export FAMILYFUND_DATA="$HOME/Library/Mobile Documents/com~apple~CloudDocs/YourFolder/data"
docker compose up --build
```

Dashboard at http://localhost:8501.

## Data Model

The single input file is `data/portfolio.csv`:

```
Date,Asset_Class,Platform,Name,Code,Currency,Exchange_Rate,Shares,Current_Price,Total_Value,Net_Cash_Flow
```

| Field | Description |
|-------|-------------|
| `Date` | Snapshot date (YYYY-MM-DD) |
| `Asset_Class` | One of 7 categories (see below) |
| `Platform` | Broker/bank name |
| `Name` | Holding name |
| `Code` | Ticker/fund code (optional) |
| `Currency` | CNY, USD, EUR, HKD |
| `Exchange_Rate` | To CNY (1.0 for CNY) |
| `Shares` | Units held |
| `Current_Price` | Price in native currency |
| `Total_Value` | Market value in CNY |
| `Net_Cash_Flow` | External cash flow (Cash rows only) |

### Asset Classes

| Code | Description |
|------|-------------|
| `US_Index_Fund` | US index funds (S&P 500, Nasdaq QDII) |
| `CN_Index_Fund` | China A-share index funds |
| `ETF_Stock` | ETFs and individual stocks |
| `Fixed_Income` | Bonds, money market funds |
| `Gold` | Physical gold, paper gold, gold ETF |
| `Company_Stock` | Employer stock (may be foreign currency) |
| `Cash` | Fixed cash reserve (100k CNY) |

### Cash Flow Convention

The fund tracks **investment portfolio + fixed cash reserve (100,000 CNY)**, not total household wealth. Salary, rent, and daily spending happen outside the fund boundary.

External cash flows go on the row where the value enters:

- **Deliberate deposit/withdrawal** → Cash row NCF (e.g., +50,000 when adding money to the fund)
- **SAP Own SAP vesting** → Company_Stock "Own SAP" row NCF = Cost_CNY (the cash you sacrificed by opting in)
- **SAP Move SAP vesting** → Company_Stock "Move SAP" row NCF = CNY value at vesting (external value entering the fund)
- **Internal rebalancing** (sell ETF → buy bonds) → NCF = 0 on all rows

This ensures NAV purely reflects investment performance. Own SAP employer subsidy shows as NAV growth (you paid less than market value). Move SAP vesting doesn't inflate NAV (it's a gift, not investment gain).

## How NAV Works

```
Day 0:  NAV = 1.0, Shares = Total_Value
Day N:  real_value = Total_Value - Net_Cash_Flow
        NAV = real_value / previous_shares
        new_shares = Net_Cash_Flow / NAV
        total_shares += new_shares
```

Cash flows only change **shares**, not **NAV**. This means NAV purely reflects investment performance, independent of when you add or withdraw money.

## Project Structure

```
├── dashboard/app.py          # Streamlit dashboard (4 tabs: Dashboard, Weekly Update, History, SAP Stock)
├── src/
│   ├── nav_engine.py         # Core NAV engine, cost basis, file I/O
│   ├── sap_stock.py          # SAP stock cost basis engine (Own SAP / Move SAP)
│   ├── import_sap_xlsx.py    # XLSX → own_sap/move_sap CSV migration
│   ├── fx_service.py         # Live exchange rate fetcher
│   ├── asset_breakdown.py    # XLSX parser
│   └── migrate_xlsx.py       # XLSX → CSV migration tool
├── data/
│   └── portfolio_sample.csv  # Sample data (3 weeks × 3 assets)
├── tests/                    # 98 pytest tests
├── Dockerfile
├── docker-compose.yml
└── .streamlit/config.toml
```

## Architecture

See [ARCHITECTURE_shared.md](ARCHITECTURE_shared.md) for the full design document covering algorithms, data flow, and CIO workflow.

## License

MIT
