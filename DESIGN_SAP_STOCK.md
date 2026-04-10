# SAP Stock Options — Design Document

## Overview

SAP offers two equity compensation programs. Each has a distinct cost basis model:

| Program | Type | Who Pays | Cost Basis Logic |
|---------|------|----------|-----------------|
| **Own SAP** | ESPP-like (Match + Purchase) | Employee + Employer | Opportunity cost = tax on employer match portion |
| **Move SAP** | RSU | SAP grants free shares | FMV at vesting date (cost = 0 to employee, taxed as income) |

Both programs also generate **dividend reinvestment** (fractional share purchases) and Own SAP has occasional **sell** events.

---

## Data Source

**File:** `CurrentAsset.xlsx`
- Sheet2: Own SAP (149 rows, 9 columns)
- Sheet3: Move SAP (46 rows, 8 columns)

### Own SAP Schema (Sheet2)

| Column | Description |
|--------|-------------|
| `Date` | Transaction date |
| `Activity` | `Match`, `Purchase`, `Purchase (Dividend)`, `Sell` |
| `Vehicle Description` | Always "SAP" (or dividend description) |
| `CNY` | Total value of shares in CNY (= Price × Quantity × FX_Rate) |
| `Cost` | True cost in CNY = `CNY × Discount_Ratio` |
| `Discount Ratio` | Cost ratio — Match: 0.20-0.25 (= marginal tax rate), Purchase: 0.75-0.80 (= employee's cash portion) |
| `Purchase/Sell price` | Share price in EUR |
| `Quantity` | Shares acquired (negative for sells) |
| `Total Euro` | Amount in EUR |

**Activity types:**
- **Match**: SAP matches your purchase with free shares. You pay only the income tax on these shares. `Discount_Ratio` = marginal tax rate. `Cost = CNY × tax_rate`.
- **Purchase**: You buy shares with your own cash. `Discount_Ratio` = employee's cash portion (0.75-0.80). `Cost = CNY × discount_ratio` = the cash you forgo by opting in.
- **Purchase (Dividend)**: Dividend reinvestment, Discount Ratio = 1.0 (no subsidy, full cost).
- **Sell**: Share sale, negative quantity.

**Marginal tax rates** (from summary rows):
| Year | Rate |
|------|------|
| 2021-2023 | 20% |
| 2024-2025 | 25% |

**Current totals:** 199.38 shares, cost 107,413.12 CNY, break-even price 68.45 EUR.

### Move SAP Schema (Sheet3)

| Column | Description |
|--------|-------------|
| `Date` | Vesting/purchase date |
| `Activity` | `Award`, `Purchase` (dividend reinvestment) |
| `Vehicle Description` | "SAP" or "Move SAP Dividend" |
| `Purchase/Sell price` | FMV at vesting (EUR) |
| `Quantity` | Shares vested |
| `Ratio` | EUR/CNY exchange rate at vesting |
| `CNY` | Value in CNY (= Price x Quantity x Ratio) |
| `euro` | Value in EUR |

**Vesting pattern:** 4 awards per quarter (from 4 different grant tranches), each vesting on the 11th of Mar/Jun/Sep/Dec.

**Current totals:** 207.77 shares, CNY 316,658.65.

---

## Cost Basis Algorithms

### Own SAP — Opportunity Cost Model

The key insight: if you **don't** opt in, you receive the Purchase CNY amount as cash compensation. If you **do** opt in:

1. **Purchase row**: You forgo receiving that cash → your cost = `CNY × Discount_Ratio` (the cash you would have received)
2. **Match row**: SAP gives you free shares, but you owe income tax on them → your cost = `CNY × Discount_Ratio` (= match value × marginal tax rate)

**The formula is the same for all rows:**

```
Cost = CNY × Discount_Ratio
```

The `Cost` column in the spreadsheet already stores this value.

**Opt-in vs. Opt-out (March 5, 2026 example):**

| Scenario | What happens | Net cost |
|----------|-------------|----------|
| **Opt-out** | Receive 2,771.175 CNY cash in bank account | 0 (you just get paid) |
| **Opt-in** | Forgo 2,771.175 CNY cash (Purchase cost) + pay 410.59 CNY tax (Match cost) → get 3.9184 shares worth 5,337.26 CNY | 3,181.77 CNY |

**Breakdown:**
- Purchase: CNY=3,694.90, Discount_Ratio=0.75 → Cost = 3,694.90 × 0.75 = **2,771.175** (forgone cash)
- Match: CNY=1,642.36, Discount_Ratio=0.25 → Cost = 1,642.36 × 0.25 = **410.59** (tax on free shares)
- Total cost: **3,181.77 CNY** for 3.9184 shares

**Per-transaction cost calculation:**

```python
for each transaction:
    if Activity in ("Match", "Purchase", "Purchase (Dividend)"):
        cost_cny = CNY * Discount_Ratio   # already in Cost column
    
    elif Activity == "Sell":
        # Reduce cost basis proportionally (average cost method)
        shares_sold = abs(Quantity)
        cost_reduction = avg_cost_per_share * shares_sold
```

**Aggregate cost basis:**

```
total_cost_cny = sum(Cost) for all buy transactions
               - cost_reduction for sells
```

### Move SAP (RSU) — FMV at Vesting

RSUs are granted at zero cost to the employee. At vesting, the shares are taxed as ordinary income at their fair market value. The tax is withheld automatically by SAP (not tracked here).

**Per-transaction cost:**

```python
for each Award:
    cost_cny = Price × Quantity × Ratio   # = CNY column value
    # This is the FMV at vesting, which is your tax basis

for each Purchase (Dividend):
    cost_cny = Price × Quantity × Ratio   # full price, no subsidy
```

The `CNY` column in Sheet3 already represents the cost basis for each vesting event.

**Aggregate cost basis:**

```
total_cost_cny = sum(CNY) for all transactions
```

Current total: **316,658.65 CNY** for 207.77 shares.

---

## Data Model — Three Separate CSVs

Each data source has a different schema and cadence, so they stay in separate files:

### `data/portfolio.csv` (existing, unchanged)

Weekly snapshots. Company_Stock rows carry current market value only:
```
2026-04-07,Company_Stock,SAP,Own SAP,,EUR,8.05,199.38,170.00,271844.76,0
2026-04-07,Company_Stock,SAP,Move SAP,,EUR,8.05,207.77,170.00,283604.18,0
```
No cost basis here — `Net_Cash_Flow = 0` (internal asset, no external cash flow through Cash row).

### `data/own_sap.csv` (new)

Event-based Own SAP (ESPP) transactions:
```
Date,Activity,Price_EUR,Quantity,Discount_Ratio,CNY,Cost_CNY
```

| Field | Description |
|-------|-------------|
| `Date` | Transaction date (YYYY-MM-DD) |
| `Activity` | `Match`, `Purchase`, `Dividend`, `Sell` |
| `Price_EUR` | Share price in EUR |
| `Quantity` | Shares (negative for sells) |
| `Discount_Ratio` | 0.20-0.25 for Match (= tax rate), 0.75-0.80 for Purchase, 1.0 for Dividend |
| `CNY` | Total value in CNY (Price × Quantity × FX rate) |
| `Cost_CNY` | True cost = `CNY × Discount_Ratio` |

### `data/move_sap.csv` (new)

Event-based Move SAP (RSU) transactions:
```
Date,Activity,Price_EUR,Quantity,FX_Rate,CNY
```

| Field | Description |
|-------|-------------|
| `Date` | Vesting/purchase date (YYYY-MM-DD) |
| `Activity` | `Award`, `Dividend` |
| `Price_EUR` | FMV at vesting in EUR |
| `Quantity` | Shares vested |
| `FX_Rate` | EUR→CNY rate at vesting |
| `CNY` | Cost basis = `Price × Quantity × FX_Rate` |

No `Discount_Ratio` or `Cost_CNY` — for RSUs the CNY column **is** the cost basis (FMV at vesting).

### Why three files?

| | `portfolio.csv` | `own_sap.csv` | `move_sap.csv` |
|---|---|---|---|
| **Granularity** | Weekly snapshot | Monthly events | Quarterly events |
| **Schema** | 11 columns, all asset classes | 7 columns, ESPP-specific | 6 columns, RSU-specific |
| **Cost model** | NCF-based (general) | CNY × Discount_Ratio | FMV at vesting |
| **Linkage** | Market value for P/L | Cost basis for Own SAP | Cost basis for Move SAP |

---

## Implementation Plan

### Phase 1: SAP Stock Engine (`src/sap_stock.py`)

New module with these functions:

```python
# --- Own SAP ---
def load_own_sap(csv_path='data/own_sap.csv'):
    """Load own_sap.csv → DataFrame."""

def compute_own_sap_cost(row):
    """Compute true cost (CNY) for a single Own SAP transaction.
    cost = CNY × Discount_Ratio for all buy activities.
    Sell: reduce basis using average cost method."""

def own_sap_summary(df):
    """Return: total shares, total cost, avg cost/share (CNY & EUR), break-even EUR."""

# --- Move SAP ---
def load_move_sap(csv_path='data/move_sap.csv'):
    """Load move_sap.csv → DataFrame."""

def move_sap_summary(df):
    """Return: total shares, total cost (= sum of CNY), avg cost/share."""

# --- Combined ---
def compute_sap_cost_basis(own_csv, move_csv):
    """Return dict with per-program and combined cost basis for Company_Stock P/L."""
```

### Phase 2: XLSX Import Script (`src/import_sap_xlsx.py`)

One-time migration from CurrentAsset.xlsx to two CSVs:

```python
def import_own_sap(xlsx_path, sheet_index=1):
    """Read Sheet2 → data/own_sap.csv"""

def import_move_sap(xlsx_path, sheet_index=2):
    """Read Sheet3 → data/move_sap.csv"""
```

This runs once to bootstrap. After that, new transactions are appended directly to the CSVs.

### Phase 3: Dashboard Integration (`dashboard/app.py`)

Add a **"Company Stock"** section to the Dashboard tab (or a new 4th tab):

1. **Summary KPIs:**
   - Own SAP: shares, cost basis, current value, P/L, P/L%
   - Move SAP: shares, cost basis, current value, P/L, P/L%
   - Combined: total shares, total cost, total value, total P/L

2. **Transaction History Table:**
   - Filterable by program (Own SAP / Move SAP)
   - Columns: Date, Activity, Price, Quantity, Cost

3. **Cost Basis Waterfall Chart:**
   - Horizontal bar: employee paid vs. opportunity cost vs. employer match (Own SAP)
   - Stacked over time to show cost accumulation

4. **Price vs. Break-Even Chart:**
   - Current SAP share price (from portfolio.csv or live fetch) vs. break-even price
   - Mark in green if above break-even, red if below

### Phase 4: Add New Transactions via UI

In the Weekly Update tab (or a separate "SAP Stock" section), two sub-forms:

#### Own SAP — Monthly Entry

User copies info from broker statement. No FX rate needed — broker already resolved everything into CNY and shares.

**Shared fields (top):**
- Date (date picker)
- Price EUR (from broker)
- Tax Rate (preset, e.g. 0.25 — editable if marginal rate changes)

**Per-row fields (dynamic, [+ Add Row]):**
- Type: Match / Purchase / Dividend / Sell (select)
- CNY: contribution amount from broker
- Quantity: shares from broker

**Auto-derived (read-only):**
- Discount: Match → tax rate, Purchase → 1 - tax rate, Dividend → 1.0
- Cost: CNY × Discount

```
┌──────────────────────────────────────────────────────────────┐
│  Own SAP — Monthly Entry                                     │
├──────────────────────────────────────────────────────────────┤
│  Date: [2026-04-07]    Price (EUR): [168.24]                 │
│  Tax Rate: [0.25]                                            │
│                                                              │
│  │ Type     │ CNY        │ Qty      ║ Discount │ Cost      │ │
│  │ Match    │ [1,642.36] │ [1.2057] ║ 0.25     │   410.59  │ │
│  │ Purchase │ [3,694.90] │ [2.7126] ║ 0.75     │ 2,771.18  │ │
│  [+ Add Row]                                                 │
│                                                              │
│  Total: 3.9183 shares, Cost: 3,181.77 CNY                   │
│  [Save]                                                      │
└──────────────────────────────────────────────────────────────┘
```

**Typical monthly input: Date + Price + 2×(Type, CNY, Qty) = 8 fields.**
Type defaults to Match for first row, Purchase for second, so in practice just **Date, Price, 2×CNY, 2×Qty = 6 fields**.

#### Move SAP — Quarterly Vesting

User enters vesting date, price, and each tranche quantity. FX rate is needed to compute CNY cost basis.

**Shared fields (top):**
- Date (date picker)
- Price EUR (FMV at vesting)
- [Refresh FX Rate] → EUR/CNY rate (from fx_service, editable for manual override)

**Per-row fields (dynamic, [+ Add Row]):**
- Quantity: shares vested per tranche

**Auto-derived (read-only):**
- CNY: Price × Quantity × FX_Rate (= cost basis for each tranche)

```
┌──────────────────────────────────────────────────────────────┐
│  Move SAP — Quarterly Vesting                                │
├──────────────────────────────────────────────────────────────┤
│  Date: [2026-06-11]    Price (EUR): [180.00]                 │
│  [Refresh FX Rate]  EUR/CNY: [8.10]                          │
│                                                              │
│  │ # │ Qty      ║ CNY       │                                │
│  │ 1 │ [6.0722] ║ 8,856.85  │                                │
│  │ 2 │ [5.9655] ║ 8,700.74  │                                │
│  │ 3 │ [3.3316] ║ 4,857.47  │                                │
│  │ 4 │ [2.5247] ║ 3,681.05  │                                │
│  [+ Add Row]                                                 │
│                                                              │
│  Total: 17.894 shares, CNY: 26,095.11                        │
│  [Save]                                                      │
└──────────────────────────────────────────────────────────────┘
```

**Typical quarterly input: Date + Price + FX Rate + N quantities.**

#### Input Summary

| Event | User types | Derived |
|-------|-----------|---------|
| Own SAP monthly | Date, Price, 2×(Type, CNY, Qty) | Discount, Cost |
| Move SAP quarterly | Date, Price, FX Rate, N×Qty | CNY per tranche |
| Dividend | Date, Price, Qty (+ FX for Move SAP) | Cost |
| Sell | Date, Price, Qty | Cost reduction (avg cost) |

Workflow:
1. User selects program tab (Own SAP / Move SAP)
2. Fills in shared fields + per-row fields from broker statement
3. Reviews auto-computed columns
4. Click "Save" → appends to `own_sap.csv` or `move_sap.csv` using `_atomic_write_csv`
5. Dashboard reloads with updated summary

### Phase 5: Integration with Main Cost Basis

Modify `compute_cost_basis()` in `nav_engine.py`:
- For `Company_Stock` rows in `portfolio.csv`, instead of using `Net_Cash_Flow` sum (which is 0 for rebalancing), pull the cost basis from `own_sap.csv` + `move_sap.csv` via `compute_sap_cost_basis()`.
- This gives accurate P/L for company stock that accounts for the opportunity cost model.

```python
def compute_cost_basis(df, own_sap_csv=None, move_sap_csv=None):
    # ... existing logic ...
    
    if own_sap_csv or move_sap_csv:
        sap_basis = compute_sap_cost_basis(own_sap_csv, move_sap_csv)
        # Override Company_Stock rows with SAP-derived cost basis
        for idx, row in result.iterrows():
            if row['Asset_Class'] == 'Company_Stock':
                name = row['Name']
                if 'Own' in name and 'own_sap' in sap_basis:
                    result.at[idx, 'Cost_Basis'] = sap_basis['own_sap']['total_cost']
                elif 'Move' in name and 'move_sap' in sap_basis:
                    result.at[idx, 'Cost_Basis'] = sap_basis['move_sap']['total_cost']
    
    return result
```

---

## Adding New Transactions — Quick Reference

### Own SAP (Monthly)

When you receive your monthly ESPP statement:

1. Open Dashboard → SAP Stock tab
2. Add **Match** row: date, price, quantity, discount ratio (check payslip)
3. Add **Purchase** row: date, price, quantity, discount ratio
4. Save → cost computed automatically

Or manually append to `data/own_sap.csv`:
```csv
2026-04-07,Match,168.24,1.2057,0.25,1642.36,410.59
2026-04-07,Purchase,168.24,2.7126,0.75,3694.90,2771.18
```

### Move SAP (Quarterly, on 11th of Mar/Jun/Sep/Dec)

When RSUs vest:

1. Open Dashboard → SAP Stock tab
2. Add 4 **Award** rows (one per grant tranche): date, price, quantity
3. Save → cost = FMV at vesting, computed automatically

Or manually append to `data/move_sap.csv`:
```csv
2026-06-11,Award,180.00,6.0722,8.10,8856.85
2026-06-11,Award,180.00,5.9655,8.10,8700.74
2026-06-11,Award,180.00,3.3316,8.10,4857.47
2026-06-11,Award,180.00,2.5247,8.10,3681.05
```

### Dividends (Annual, ~May)

Append to the respective CSV:
```csv
# own_sap.csv
2026-05-20,Dividend,185.00,0.5432,1.0,813.50,813.50
# move_sap.csv
2026-05-20,Dividend,185.00,0.9123,8.10,1366.73
```

### Sell (Own SAP only)

Append to `data/own_sap.csv`:
```csv
2026-07-15,Sell,210.00,-50,1.0,-84000.00,-84000.00
```
Cost basis reduced by: `avg_cost_per_share × 50 shares`.

---

## File Changes Summary

| File | Change |
|------|--------|
| `src/sap_stock.py` | **New** — SAP stock engine (cost calculation, summary) |
| `src/import_sap_xlsx.py` | **New** — one-time XLSX → CSV migration |
| `data/own_sap.csv` | **New** — Own SAP (ESPP) transaction history |
| `data/move_sap.csv` | **New** — Move SAP (RSU) transaction history |
| `dashboard/app.py` | Add Company Stock section/tab with SAP-specific UI |
| `src/nav_engine.py` | Modify `compute_cost_basis()` to pull SAP cost basis |
| `tests/test_sap_stock.py` | **New** — unit tests for cost calculation |

---

## Verification Checklist

- [ ] Import XLSX → `own_sap.csv` matches totals (199.38 shares / 107,413.12 CNY)
- [ ] Import XLSX → `move_sap.csv` matches totals (207.77 shares / 316,658.65 CNY)
- [ ] Own SAP cost = CNY × Discount_Ratio for all rows
- [ ] Move SAP cost = FMV at vesting (CNY column from Sheet3)
- [ ] Sell events reduce cost basis using average cost method
- [ ] Dashboard shows combined P/L integrating SAP cost basis
- [ ] New vesting entry via UI appends to CSV correctly
- [ ] FX rate auto-fill works for new entries
- [ ] All existing tests still pass

---

## Integration with Weekly Snapshot (`portfolio.csv`)

When new SAP transactions are recorded (monthly Own SAP vesting, quarterly Move SAP vesting, or annual dividends), the corresponding `Company_Stock` rows in `portfolio.csv` must be updated during the next weekly snapshot.

### NCF Convention for Company_Stock

| Event | `Shares` | `Total_Value` | `Net_Cash_Flow` |
|-------|----------|---------------|-----------------|
| Own SAP monthly vesting | Updated total from `own_sap.csv` | Shares × Price × FX | **Sum of `Cost_CNY`** from new transactions |
| Move SAP quarterly vesting | Updated total from `move_sap.csv` | Shares × Price × FX | **Sum of `CNY`** from new vesting rows (FMV at vesting) |
| Own SAP dividend reinvest | Updated total | Updated value | **Cost_CNY** of dividend row |
| Move SAP dividend reinvest | Updated total | Updated value | **CNY** of dividend row (full value) |
| No SAP activity this week | Same as last week | Updated value (price change) | 0 |

### Why These NCF Values?

NCF represents **external value entering the fund** — any asset that comes from outside the portfolio boundary.

**Own SAP:** NCF = Cost_CNY (what you sacrificed). The employer subsidy portion (market value minus your cost) is genuine investment performance — NAV should reflect it as a gain.

**Move SAP:** NCF = CNY (full market value at vesting). RSU shares are free to you, but they are still external value entering the fund from your employer. If NCF = 0, NAV would jump as if your investments grew — but they didn't, you received a gift. Setting NCF = FMV ensures new fund shares are issued to absorb the inflow, keeping NAV flat (correctly reflecting no investment gain from the vesting event itself). Any subsequent price change after vesting is real investment performance.

### Example: Own SAP March 2026 Vesting

New transactions in `own_sap.csv`:
```csv
2026-03-05,Match,168.24,1.2057,0.25,1642.36,410.59
2026-03-05,Purchase,168.24,2.7126,0.75,3694.90,2771.18
```

In next `portfolio.csv` snapshot:
```csv
2026-03-07,Company_Stock,SAP,Own SAP,,EUR,8.05,199.38,168.24,269315.55,3181.77
```
Where `NCF = 410.59 + 2771.18 = 3181.77` (sum of Cost_CNY from this month's new rows).

### SAP Stock Tab — Decoupled from portfolio.csv

The SAP Stock tab in the dashboard uses **its own price and FX rate inputs** (user-editable `number_input` fields), not values from `portfolio.csv` Company_Stock rows. This means:
- SAP Stock tab works even if `portfolio.csv` has no Company_Stock rows
- Price/FX can be updated independently of the weekly snapshot cycle
- A "Refresh FX" button fetches the live EUR/CNY rate from frankfurter.app
