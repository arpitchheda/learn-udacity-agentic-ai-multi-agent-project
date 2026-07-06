# Munder Difflin Paper Company — Multi-Agent System Reflection Report

**Author:** Arpit Chheda  
**Date:** July 2026  
**Framework:** smolagents (HuggingFace)  
**Model:** gpt-4o-mini via OpenAI-compatible proxy (openai.vocareum.com)

---

## 1. System Architecture Overview

The Munder Difflin multi-agent system automates three core business operations — inventory management, quote generation, and order fulfillment — using a hierarchical orchestration pattern with four specialized agents.

### Agent Workflow

```
Customer Request
      |
      v
  Orchestrator Agent (CodeAgent)
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

The **Orchestrator Agent** is the single entry point. It receives every customer request and coordinates the three worker agents in a fixed sequence:

1. **Inventory check** — discover what is actually in stock and at what price
2. **Quoting** — retrieve historical pricing benchmarks and estimate delivery
3. **Sales finalization** — record confirmed transactions and update cash balance

Worker agents operate independently with no cross-agent calls. All database access flows through the db_helpers module, which owns the shared SQLAlchemy engine.

---

## 2. Agent Responsibilities

### Orchestrator Agent — `CodeAgent`

The orchestrator uses smolagents' `CodeAgent` class, which generates executable Python code at each reasoning step rather than selecting from a fixed tool list. This makes it ideal for orchestration: it can dynamically loop over variable-length item lists, branch on stock availability, and call worker agents in any combination.

**Key responsibilities:**
- Parse the customer request text and extract the request date
- Map informal item names to exact database names using a built-in lookup table (e.g. `"A4 glossy paper"` → `"Glossy paper"`, `"heavy cardstock"` → `"100 lb cover stock"`)
- Verify stock availability by comparing requested quantities against the inventory snapshot
- Apply tiered bulk discounts: 0–500 units = 0%, 501–1,000 = 10%, 1,001+ = 15%
- Assemble a transparent, professional customer-facing response
- **Never** query the database directly — all data access is delegated

**Configuration:** `max_steps=12`, `managed_agents=[inventory_agent, quoting_agent, sales_agent]`

---

### Inventory Agent — `ToolCallingAgent`

Handles all stock-related queries. Uses `ToolCallingAgent` because its task set is small and well-defined — no dynamic code generation needed.

| Tool | Helper Function | Purpose |
|------|----------------|---------|
| `check_all_inventory` | `get_all_inventory()` | Full stock snapshot with unit prices as of a given date |
| `check_stock_level` | `get_stock_level()` | Units on hand for a single specific item |

**Configuration:** `max_steps=5`

---

### Quoting Agent — `ToolCallingAgent`

Estimates prices and delivery timelines by drawing on historical quote data.

| Tool | Helper Function | Purpose |
|------|----------------|---------|
| `search_quote_history_tool` | `search_quote_history()` | Find comparable past quotes; filters out rows where `total_amount = -1` |
| `estimate_delivery` | `get_supplier_delivery_date()` | Calculate delivery lead time based on order quantity |
| `get_item_unit_prices` | inventory table (direct SQL) | Exact unit price lookup for specific items |

Lead time rules: ≤10 units → same day, ≤100 → +1 day, ≤1,000 → +4 days, >1,000 → +7 days.

**Configuration:** `max_steps=6`

---

### Sales Agent — `ToolCallingAgent`

Records confirmed transactions and enforces a financial safety rule before any stock purchases.

| Tool | Helper Function | Purpose |
|------|----------------|---------|
| `finalize_sale` | `create_transaction()` | Record a completed customer sale (`transaction_type='sales'`) |
| `approve_purchase` | `get_cash_balance()` | Enforce 20% cash safety margin before approving any stock spend |
| `check_cash_balance` | `get_cash_balance()` | Return current cash position |
| `calculate_financial_health` | `generate_financial_report()` | Rate business health: EXCELLENT / GOOD / FAIR / POOR |
| `generate_report` | `generate_financial_report()` | Full financial summary: cash, inventory value, top sellers |

**Financial safety rule:** A stock purchase is approved only when `(balance − cost) ≥ balance × 0.20`. This prevents the business from spending itself into illiquidity.

**Configuration:** `max_steps=8`

---

## 3. Design Decisions

### Why CodeAgent for the orchestrator?

`CodeAgent` generates Python code at each step, while `ToolCallingAgent` selects from a fixed tool list. The orchestrator needs to iterate over a variable number of line items, conditionally call different agents, and build a formatted response — tasks that map naturally to code. Using `ToolCallingAgent` here would require a different tool for every possible routing combination, making the system brittle.

### Why ToolCallingAgent for worker agents?

Each worker agent has a small, clearly scoped tool set (2–5 tools). `ToolCallingAgent` is simpler, more predictable, and easier to debug for leaf-node tasks that do one thing well. It also uses fewer tokens per step since it doesn't generate full Python programs.

### Why a modular package structure?

All agent code lives in `agents/` with one file per agent (`inventory_agent.py`, `quoting_agent.py`, `sales_agent.py`, `orchestrator_agent.py`). Shared infrastructure (`db_engine` and all 7 helper functions) lives in `db_helpers.py`. This prevents circular imports and means each agent can be tested in isolation by importing only what it needs.

### Why checkpoint/resume?

Processing 20 requests with an LLM can take 30–60 minutes and is vulnerable to rate limits, network drops, or budget exhaustion. The harness writes `test_results.csv` incrementally after every request. On restart without `--force-rerun` it reads already-completed request IDs and skips them, so a crash mid-run costs only the in-flight request.

### Why exponential backoff retry?

Rate limit errors (HTTP 429) are transient — the right response is to wait, not fail. The `run_with_retry()` function retries up to 5 times with delays of 2, 4, 8, 16, 32 seconds. Budget and authentication errors are non-retryable and immediately logged as `[ERROR]` so the run continues with the next request rather than looping forever.

---

## 4. Evaluation Results

The system was evaluated against all 20 requests in `quote_requests_sample.csv` covering April 1–17, 2025.

### Financial Summary

| Metric | Value |
|--------|-------|
| Starting cash balance | $45,059.70 |
| Final cash balance | $45,859.60 |
| Net revenue | **+$799.90** |
| Starting inventory value | $4,940.30 |
| Final inventory value | $4,140.40 |
| Total assets (final) | $50,000.00 |

### Request Outcomes

| Request | Date | Cash After | Status | Notes |
|---------|------|-----------|--------|-------|
| 1 | Apr 01 | $45,124.70 | Fulfilled | Glossy paper, Cardstock, Colored paper — delivery Apr 2 |
| 2 | Apr 03 | $45,624.70 | Partial | Large poster paper fulfilled; streamers + balloons OOS — delivery Apr 7 |
| 3 | Apr 04 | $45,624.70 | Declined | A4 insufficient; A3 paper + printer paper not in inventory |
| 4 | Apr 05 | $45,637.20 | Partial | A4 paper fulfilled; recycled cardstock OOS — delivery Apr 6 |
| 5 | Apr 05 | $45,732.20 | Partial | Colored paper + Cardstock fulfilled; washi tape OOS |
| 6 | Apr 06 | $45,732.20 | Declined | All 3 items out of stock |
| 7 | Apr 07 | $45,732.20 | Declined | Glossy, poster paper, cardstock all insufficient qty |
| 8 | Apr 07 | $45,809.60 | Partial | Glossy paper (387 units) fulfilled; matte, colored, recycled OOS |
| 9 | Apr 07 | $45,809.60 | Declined | A4 paper insufficient; A3 glossy + kraft envelopes OOS |
| 10 | Apr 08 | $45,809.60 | Declined | A4 paper + cardstock both OOS |
| 11 | Apr 08 | $45,809.60 | Declined | Cardstock + A4 paper insufficient; napkins OOS |
| 12 | Apr 08 | $45,809.60 | Declined | Glossy paper OOS; cardstock insufficient |
| 13 | Apr 08 | $45,809.60 | Declined | A3 glossy + A4 matte not in inventory |
| 14 | Apr 09 | $45,809.60 | Declined | A4 paper, poster paper, cardstock all insufficient |
| 15 | Apr 12 | $45,809.60 | Declined | All 3 large-qty items insufficient |
| 16 | Apr 13 | $45,809.60 | Declined | A4 paper + colored paper insufficient; poster paper OOS |
| 17 | Apr 14 | $45,859.60 | Partial | Paper plates fulfilled; A4, A3, napkins, cups OOS — delivery Apr 14 |
| 18 | Apr 14 | $45,859.60 | Declined | Cardstock, printing paper, colored paper all insufficient |
| 19 | Apr 15 | $45,859.60 | Declined | A4 glossy, A3 matte, cardstock all OOS |
| 20 | Apr 17 | $45,859.60 | Declined | Flyers + tickets not in inventory; poster paper insufficient |

**Cash balance changes: 5 requests** (req 1, 2, 4, 5, 8, 17 — requirement: ≥3) ✓  
**Successfully fulfilled quotes: 5 requests** (req 1, 2, 4, 5, 8, 17 — requirement: ≥3) ✓  
**Unfulfilled/partial requests: 15 requests** with specific reasons provided ✓  
**Stale delivery dates (2023): 0** — all delivery dates correctly anchored to 2025 ✓

---

## 5. Strengths of the Implementation

### Transparent and accurate customer communication
Every fulfilled response includes a line-item breakdown with unit prices, quantities, totals, and delivery dates anchored to the actual request date. All delivery dates are on or after the request date (validated in the orchestrator). When items cannot be supplied, the response states the exact reason and available quantity (e.g. request 14: "A4 paper: requested 5000, available 22"). No internal data — DB row IDs, profit margins, SQL errors — is ever exposed.

### Correct response/state reconciliation
The orchestrator tracks confirmed sales from the sales agent before writing the final response. This ensures the customer reply always matches what was actually recorded in the database — a customer is never told their order failed if a transaction was successfully written.

### Partial fulfillment handling
Rather than rejecting an entire order when one item is unavailable, the system fulfills whatever it can and explains each gap individually. Requests 2, 4, 5, 8, and 17 all demonstrate partial fulfillment with clear breakdowns of fulfilled vs. unfulfilled items and reasons for each gap.

### Robust item name resolution
The inventory seeded ~18 of 42 possible products with exact names like `"100 lb cover stock"` and `"Large poster paper (24x36 inches)"`. Customer requests used informal language like `"heavy cardstock"` and `"poster board"`. The orchestrator's name-mapping table resolved these reliably across all 20 requests, and items that couldn't be mapped were correctly flagged as unavailable.

### Financial discipline
The 20% cash safety margin in `approve_purchase` acted as an automated financial control throughout the test period. Cash grew from $45,059.70 to $45,859.60 while the reserve was never breached. The `calculate_financial_health` tool rated the company's position as EXCELLENT (cash-to-assets ratio above 90%) for the duration of the run.

### Operational resilience
The checkpoint/resume mechanism writes `test_results.csv` incrementally after every request. When a run was interrupted mid-way (encoding error on Windows cp1252 from a Unicode character in agent output), resuming without `--force-rerun` skipped all completed requests and continued from where it left off — zero data loss.

---

## 6. Areas for Improvement

### 1. Automatic stock reorder triggering
When a sale reduces an item's stock below its `min_stock_level`, the system currently takes no action. A reorder tool in the inventory or sales agent could automatically create a `stock_orders` transaction to replenish stock, keeping the business self-sustaining without manual intervention.

### 2. Embedding-based item name resolution
The current name-mapping table in the orchestrator prompt is a static list maintained manually. As the catalogue grows or changes, the prompt needs updates. Replacing it with an embedding similarity search against the live inventory snapshot would handle new product names, typos, and multilingual requests dynamically — without prompt engineering overhead.

### 3. Customer negotiation agent (5th agent)
A fifth agent could read the customer's mood and role from the request context (`mood`, `job` columns in `quote_requests`) and actively negotiate: proposing alternatives, offering back-order delivery windows, or splitting an order across multiple shipments. This would convert some of the 11 declined/partial requests into full sales.

### 4. Business advisor agent
After processing all requests, a business advisor agent could call `generate_financial_report()` and `search_quote_history()` to produce a management summary: identifying slow-moving stock categories (e.g. `Crepe paper` with 234 units and zero sales), flagging demand-supply mismatches, and recommending pricing or restock actions.

---

## 7. Conclusion

The Munder Difflin multi-agent system successfully automates inventory checking, quote generation, and order fulfillment for a paper supply business. The four-agent architecture — orchestrator plus three specialist workers — cleanly separates concerns, uses all 7 required helper functions, and produces transparent, justified responses for every customer interaction.

The evaluation across 20 live test requests demonstrated that the system handles partial fulfillment gracefully, applies financial controls automatically, and recovers from transient failures without data loss. The 5 cash balance changes and $799.90 net revenue across the test period confirm that the core business logic is working correctly. All delivery dates are correctly anchored to the request date — zero stale 2023 dates remain — and every customer response accurately reflects what was actually recorded in the database.

The primary area for future investment is smarter demand handling: automated reorder triggers, embedding-based name resolution, and negotiation capability would convert more of the 15 declined/partial requests into completed sales and make the system production-ready.

---

## 8. Project File Structure

```
project/
├── project_starter.py          # Evaluation harness — run this
├── db_helpers.py               # Shared db_engine + all 7 helper functions
├── agents/
│   ├── __init__.py             # Package exports
│   ├── model_config.py         # LLM model + run_with_retry()
│   ├── orchestrator_agent.py   # CodeAgent — entry point, coordinates workers
│   ├── inventory_agent.py      # ToolCallingAgent — stock queries
│   ├── quoting_agent.py        # ToolCallingAgent — pricing + delivery
│   └── sales_agent.py          # ToolCallingAgent — transactions + finance
├── quotes.csv                  # Historical quote data (100 rows)
├── quote_requests.csv          # Historical customer requests (100 rows)
├── quote_requests_sample.csv   # Live test set (20 rows)
├── test_results.csv            # Generated output — one row per request
├── workflow_diagram.mmd        # Mermaid source for the architecture diagram
├── workflow_diagram.html       # Browser-renderable workflow diagram
├── design_notes.txt            # Architecture notes and evaluation summary
├── reflection_report.md        # This document
└── .env                        # API key (not committed)
```
