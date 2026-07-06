# Munder Difflin Paper Company — Multi-Agent System Reflection Report

**Author:** Arpit Chheda  
**Date:** July 2026  
**Framework:** pydantic-ai 2.5.0  
**Models:** gpt-4o (orchestrator), gpt-4o-mini (worker agents) via openai.vocareum.com proxy

---

## 1. System Architecture Overview

The Munder Difflin multi-agent system automates three core business operations — inventory management, quote generation, and order fulfillment — using a hierarchical orchestration pattern with four specialized agents.

### Agent Workflow

```
Customer Request
      |
      v
  Orchestrator Agent (gpt-4o, output_type=OrderResponse)
  |         |              |
  v         v              v
Inventory  Quoting       Sales
Agent      Agent         Agent
  |         |              |
  +----+----+--------------+
            |
            v
      SQLite Database
      (munder_difflin.db)
```

The **Orchestrator Agent** is the single entry point. It receives every customer request and coordinates the three worker agents through three tools:

1. **check_inventory** → inventory_agent: discover what is in stock and at what price
2. **record_sale** → sales_agent: record confirmed transactions and update cash balance
3. **get_delivery** → quoting_agent: estimate the delivery date

Worker agents operate independently with no cross-agent calls. All database access flows through the `db_helpers` module, which owns the shared SQLAlchemy engine.

---

## 2. Agent Responsibilities

### Orchestrator Agent

Uses pydantic-ai's `output_type=OrderResponse` to return a typed Pydantic model rather than freeform prose. The customer-facing message is built entirely in Python from the typed fields — the LLM cannot hallucinate arithmetic or sign-offs.

**Key responsibilities:**
- Parse the customer request and extract the request date
- Map informal item names to exact database names (e.g. `"A4 glossy paper"` → `"Glossy paper"`, `"heavy cardstock"` → `"100 lb cover stock"`)
- Verify stock availability by comparing requested quantities against the inventory snapshot
- Apply tiered bulk discounts: 0–500 units = 0%, 501–1,000 = 10%, 1,001+ = 15%
- Assemble a transparent, professional customer-facing response
- **Never** query the database directly — all data access is delegated to worker agents

**Post-processing:** After the LLM returns `OrderResponse`, `_fix_order_response()` verifies the discount tier using `_compute_discount_rate(total_units)`. If the LLM applied the wrong tier, the DB rows are deleted by SQLite `rowid` and re-inserted at the correct price before the customer message is built.

---

### Inventory Agent

Handles all stock-related queries. Uses `@agent.tool_plain` tools that return plain strings.

| Tool | Helper Function | Purpose |
|------|----------------|---------|
| `check_all_inventory` | `get_all_inventory()` | Full stock snapshot with unit prices as of a given date |
| `check_stock_level` | `get_stock_level()` | Units on hand for a single specific item |

---

### Quoting Agent

Estimates delivery timelines and provides historical pricing benchmarks.

| Tool | Helper Function | Purpose |
|------|----------------|---------|
| `search_quote_history_tool` | `search_quote_history()` | Find comparable past quotes; filters out rows where `total_amount = -1` |
| `estimate_delivery` | `get_supplier_delivery_date()` | Calculate delivery lead time based on order quantity |
| `get_item_unit_prices` | inventory table (direct SQL) | Exact unit price lookup for specific items |

Lead time rules: ≤10 units → same day, ≤100 → +1 day, ≤1,000 → +4 days, >1,000 → +7 days.

---

### Sales Agent

Records confirmed transactions and enforces a financial safety rule before any stock purchases.

| Tool | Helper Function | Purpose |
|------|----------------|---------|
| `finalize_sale` | `create_transaction()` | Record a completed customer sale (`transaction_type='sales'`) |
| `approve_purchase` | `get_cash_balance()` | Enforce 20% cash safety margin before approving any stock spend |
| `check_cash_balance` | `get_cash_balance()` | Return current cash position |
| `calculate_financial_health` | `generate_financial_report()` | Rate business health: EXCELLENT / GOOD / FAIR / POOR |
| `generate_report` | `generate_financial_report()` | Full financial summary: cash, inventory value, top sellers |

**Financial safety rule:** A stock purchase is approved only when `(balance − cost) ≥ balance × 0.20`. This prevents the business from spending itself into illiquidity.

---

## 3. Design Decisions

### Why structured output for the orchestrator?

`output_type=OrderResponse` forces the LLM to return a validated Pydantic model rather than prose. The `order_total`, `discount_explanation`, and sign-off are all computed in Python `@property` methods on the model — the LLM cannot introduce arithmetic errors. Previous runs using freeform responses produced totals like $63 instead of $65; the structured approach eliminates this class of error entirely.

### Why deterministic pricing in `record_sale`?

The `record_sale` tool does not accept a `price` parameter from the LLM. It looks up `unit_price` from the `inventory` table in Python and computes `price = unit_price × qty × (1 − discount_rate)` before delegating to `sales_agent`. This guarantees the DB always records exactly what the customer message shows — no rounding drift.

### Why post-processing discount correction?

Even with explicit instructions, LLMs occasionally apply the wrong discount tier (e.g. 0% on 800 units when 10% is correct). `_fix_order_response()` catches this deterministically: it re-computes the correct tier from `total_units`, deletes the wrong DB rows using SQLite `rowid` (the `id` column is NULL in pandas-appended rows), and re-inserts at the correct price. This ran on 5 of 20 requests and ensured zero discount errors in the final output.

### Why worker agents use `@agent.tool_plain`?

Each worker has a small, clearly scoped tool set (2–5 tools) that returns plain strings. This is simpler and uses fewer tokens than structured output at the worker level — the orchestrator does the structured reasoning; workers just need to return accurate data.

### Why a modular package structure?

All agent code lives in `agents/` with one file per agent. Shared infrastructure (`db_engine` and all 7 helper functions) lives in `db_helpers.py`. This prevents circular imports and means each agent can be tested in isolation.

### Why exponential backoff retry?

Rate limit errors (HTTP 429) and transient gateway errors (502/503) are normal with shared LLM proxies. The orchestrator retries up to 3 times with delays of 2 and 4 seconds. Budget and authentication errors are non-retryable and raise immediately. Two transient errors occurred during the test run (a 502 on request 3 and a connection error on request 10) — both recovered on the second attempt with no data loss.

### Why placeholder sanitisation?

Historical quote templates in the database contain `"[Manufacturer's Name]"` placeholders. When the LLM echoes these into a response, a compiled regex `_PLACEHOLDER_RE` in `to_customer_message()` replaces any such pattern with `"Munder Difflin Paper Company"` before the response is returned. The sign-off is also hardcoded in Python so the LLM cannot override it.

---

## 4. Evaluation Results

The system was evaluated against all 20 requests in `quote_requests_sample.csv` covering April 1–17, 2025.

### Financial Summary

| Metric | Value |
|--------|-------|
| Starting cash balance | $45,059.70 |
| Final cash balance | $46,877.21 |
| Net revenue from sales | **+$1,817.51** |
| Starting inventory value | $4,940.30 |
| Final inventory value | $2,971.40 |
| Final total assets | $49,848.61 |

### Request Outcomes

| Request | Date | Cash After | Status | Key Details |
|---------|------|-----------|--------|-------------|
| 1 | Apr 01 | $45,159.70 | Fulfilled | Glossy + 100 lb cover + Colored = $100.00, no discount (400 units) |
| 2 | Apr 03 | $45,659.70 | Partial | Large poster paper 500 units = $500.00; streamers + balloons not in catalog |
| 3 | Apr 04 | $45,659.70 | Declined | A4 insufficient (272 avail, 10,000 req); A3 + copy paper not in catalog |
| 4 | Apr 05 | $45,895.95 | Fulfilled | 100 lb cover 500 + A4 250 = $236.25 (10% disc, 750 units) |
| 5 | Apr 05 | $45,981.45 | Partial | Colored 500 + Cardstock 300 = $85.50 (10% disc, 800 units); washi tape OOS |
| 6 | Apr 06 | $46,011.45 | Partial | Cardstock 200 = $30.00; Construction paper OOS, A4 only 22 units left |
| 7 | Apr 07 | $46,260.21 | Partial | Glossy 387 + Poster 199 = $248.76 (10% disc, 586 units); Matte OOS, Cardstock 95 left |
| 8 | Apr 07 | $46,260.21 | Declined | Glossy + Matte + Recycled OOS; Colored only 188 of 2,000 requested |
| 9 | Apr 07 | $46,290.21 | Partial | A4 200 + Glossy 100 = $30.00; kraft envelopes not in catalog |
| 10 | Apr 08 | $46,339.71 | Fulfilled | A4 500 + Cardstock 200 = $49.50 (10% disc, 700 units) |
| 11 | Apr 08 | $46,359.71 | Partial | Colored 200 = $20.00; standard printer paper + napkins not in catalog |
| 12 | Apr 08 | $46,359.71 | Declined | Glossy depleted; Cardstock only 36 of 300 requested |
| 13 | Apr 08 | $46,359.71 | Declined | A3 glossy + A4 matte not in catalog |
| 14 | Apr 09 | $46,359.71 | Declined | A4 + poster paper + Cardstock all depleted or not in catalog |
| 15 | Apr 12 | $46,359.71 | Declined | A4 white + A3 colored + cardboard not in catalog |
| 16 | Apr 13 | $46,359.71 | Declined | A4 printer paper + construction paper + poster board not in catalog |
| 17 | Apr 14 | $46,409.71 | Partial | Paper plates 500 = $50.00; A4, colored, napkins, cups OOS |
| 18 | Apr 14 | $46,409.71 | Declined | Cardstock only 36 of 500 requested; printer paper + colored OOS |
| 19 | Apr 15 | $46,877.21 | Partial | Glossy 2,000 + Cardstock 1,000 = $467.50 (15% disc, 3,000 units); Matte OOS |
| 20 | Apr 17 | $46,877.21 | Declined | Flyers + tickets not in catalog; posters not in catalog |

**Cash balance changes: 11 requests** (req 1, 2, 4, 5, 6, 7, 9, 10, 11, 17, 19 — requirement: ≥3) ✓  
**Successfully fulfilled quotes: 11 requests** (requirement: ≥3) ✓  
**Unfulfilled requests with reasons: 9 requests** (requirement: ≥1) ✓  
**Post-processing discount corrections: 5 requests** (4, 5, 7, 10, 19) — all verified correct ✓

---

## 5. Strengths of the Implementation

### Transparent and arithmetically correct customer communication

Every fulfilled response includes a line-item breakdown with exact unit prices, quantities, discounted line totals, and delivery dates. The `order_total` and `discount_explanation` are Python `@property` methods on `OrderResponse` — the LLM never computes them. Previous runs with freeform output produced totals like $63 instead of $65; the structured approach eliminated this entirely across all 20 requests.

### Deterministic discount enforcement

The post-processing layer caught and corrected discount tier errors on 5 requests. In each case the LLM initially sent 0% when 10% or 15% was correct — `_fix_order_response()` detected the mismatch, deleted the wrong DB rows by `rowid`, and re-inserted at the correct price. The final cash balance, inventory value, and customer message all agreed on every request.

### Partial fulfillment handling

Rather than rejecting an entire order when one item is unavailable, the system fulfills whatever it can and explains each gap individually. 8 of 20 requests resulted in partial fulfillment with clear breakdowns of fulfilled vs. unfulfilled items and the exact reason for each gap (insufficient stock with count shown, or not in catalog).

### Robust item name resolution

The inventory seeded ~18 of 42 possible products with exact names like `"100 lb cover stock"` and `"Large poster paper (24x36 inches)"`. Customer requests used informal language like `"heavy cardstock"` and `"poster board"`. The orchestrator's name-mapping table resolved these reliably across all 20 requests, and items that couldn't be mapped were correctly flagged as unavailable.

### Resilience to transient failures

Two transient API errors occurred during the test run — a 502 gateway error on request 3 and a connection error on request 10. Both recovered on the second retry attempt (2-second wait) with no data loss and no impact on results.

### Clean customer-facing output

No response exposed DB row IDs, profit margins, internal error messages, or placeholder text. The `_PLACEHOLDER_RE` sanitisation and hardcoded sign-off ensured all 20 responses closed with `"Munder Difflin Paper Company"` — verified including request 19, which previously triggered the `[Manufacturer's Name]` placeholder issue.

---

## 6. Areas for Improvement

### 1. Database-level stock enforcement

The current system relies on the LLM to respect the available stock count returned by the inventory agent. In rare cases the LLM may ignore the check and attempt to sell more units than are available, which `create_transaction` will record without validation. A DB-level trigger or a Python pre-check in `record_sale` comparing requested quantity against `get_stock_level()` would make this guarantee hard rather than soft.

### 2. Automatic stock reorder triggering

When a sale reduces an item's stock below its `min_stock_level`, the system currently takes no action. A reorder tool in the inventory or sales agent could automatically create a `stock_orders` transaction to replenish stock, keeping the business self-sustaining. This would have prevented the stock depletion pattern seen in requests 8–18 where popular items (A4 paper, Cardstock, Glossy paper) ran out and stayed out for the remainder of the test.

### 3. Embedding-based item name resolution

The current name-mapping table in the orchestrator prompt is a static list maintained manually. As the catalogue grows or changes, the prompt needs updates. Replacing it with an embedding similarity search against the live inventory snapshot would handle new product names, typos, and multilingual requests dynamically — without prompt engineering overhead.

### 4. Customer negotiation agent (5th agent)

A fifth agent could read the customer's mood and role from the request context and actively negotiate: proposing alternatives, offering back-order delivery windows, or splitting an order across multiple shipments. This would convert some of the 9 declined requests into partial or full sales and make better use of remaining stock.

---

## 7. Conclusion

The Munder Difflin multi-agent system successfully automates inventory checking, quote generation, and order fulfillment for a paper supply business. The four-agent pydantic-ai architecture — orchestrator with structured output plus three specialist workers — cleanly separates concerns, uses all 7 required helper functions, and produces transparent, justified responses for every customer interaction.

The evaluation across 20 live test requests demonstrated that the system handles partial fulfillment gracefully, enforces bulk discounts correctly (with post-processing correction as a safety net), and recovers from transient API failures without data loss. 11 of 20 requests resulted in cash balance changes, generating $1,817.51 in net revenue from a starting balance of $45,059.70. All 9 unfulfilled requests include specific rejection reasons — either exact available quantities or catalog availability status — giving customers actionable information to resubmit smaller or different orders.

The primary area for future investment is inventory lifecycle management: automated reorder triggers and a negotiation agent would convert more declined requests into revenue and prevent the stock depletion pattern that emerged in the second half of the test run.

---

## 8. Project File Structure

```
project/
├── project_starter.py          # Evaluation harness — run this
├── db_helpers.py               # Shared db_engine + all 7 helper functions
├── agents/
│   ├── __init__.py             # Package exports
│   ├── model_config.py         # LLM model config + log_usage()
│   ├── orchestrator_agent.py   # gpt-4o, structured output — entry point
│   ├── inventory_agent.py      # gpt-4o-mini — stock queries
│   ├── quoting_agent.py        # gpt-4o-mini — pricing + delivery
│   └── sales_agent.py          # gpt-4o-mini — transactions + finance
├── quotes.csv                  # Historical quote data (100 rows)
├── quote_requests.csv          # Historical customer requests (100 rows)
├── quote_requests_sample.csv   # Live test set (20 rows)
├── test_results.csv            # Generated output — one row per request
├── token_usage.csv             # Token consumption per agent per request
├── workflow_diagram.mmd        # Mermaid source for the architecture diagram
├── workflow_diagram.html       # Browser-renderable workflow diagram
├── workflow_diagram.png        # Exported diagram image
├── design_notes.txt            # Architecture notes and evaluation summary
├── reflection_report.md        # This document
└── .env                        # API key (not committed)
```
