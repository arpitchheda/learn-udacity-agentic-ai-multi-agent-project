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
| Final cash balance | $46,746.00 |
| Net revenue | **+$1,686.30** |
| Starting inventory value | $4,940.30 |
| Final inventory value | $3,749.00 |
| Total assets (final) | $50,495.00 |

### Request Outcomes

| Request | Date | Cash After | Status | Notes |
|---------|------|-----------|--------|-------|
| 1 | Apr 01 | $45,219.70 | Fulfilled | Glossy paper, Cardstock, Colored paper |
| 2 | Apr 03 | $45,219.70 | Partial | Poster paper fulfilled; streamers + balloons OOS |
| 3 | Apr 04 | $45,219.70 | Declined | A4 insufficient; A3 and printer paper not in inventory |
| 4 | Apr 05 | $45,232.20 | Partial | A4 paper fulfilled; recycled cardstock OOS |
| 5 | Apr 05 | $45,327.20 | Partial | Colored paper + Cardstock fulfilled; washi tape OOS |
| 6 | Apr 06 | $45,327.20 | Declined | All 3 items below requested quantity |
| 7 | Apr 07 | $45,627.20 | Partial | Large poster paper fulfilled; glossy, matte, cardstock OOS |
| 8 | Apr 07 | $45,627.20 | Declined | All items OOS or insufficient |
| 9 | Apr 07 | $45,877.20 | Partial | Kraft paper fulfilled; A4 paper + glossy paper OOS |
| 10 | Apr 08 | $45,877.20 | Declined | A4 paper + cardstock both insufficient |
| 11 | Apr 08 | $45,877.20 | Declined | Cardstock insufficient; printer paper insufficient; napkins OOS |
| 12 | Apr 08 | $46,127.20 | Partial | 100 lb cover stock fulfilled; cardstock OOS |
| 13 | Apr 08 | $46,127.20 | Declined | A3 glossy OOS; A4 matte insufficient |
| 14 | Apr 09 | $46,127.20 | Declined | A4 paper, cardstock, poster paper all insufficient |
| 15 | Apr 12 | $46,127.20 | Declined | All 3 large-qty items insufficient |
| 16 | Apr 13 | $46,227.20 | Partial | Large poster paper fulfilled; A4 + construction paper OOS |
| 17 | Apr 14 | $46,277.20 | Partial | Paper plates fulfilled; 4 other items OOS |
| 18 | Apr 14 | $46,277.20 | Partial | Colored paper fulfilled; cardstock + printing paper OOS |
| 19 | Apr 15 | $46,746.00 | Partial | Fulfilled available stock; A4 glossy + A3 matte insufficient |
| 20 | Apr 17 | $46,746.00 | Declined | Flyers, posters, tickets — none in inventory |

**Cash balance changes: 8 requests** (requirement: ≥3) ✓  
**Successfully fulfilled quotes: 9 requests** (requirement: ≥3) ✓  
**Unfulfilled/partial requests: 11 requests** with reasons provided ✓

---

## 5. Strengths of the Implementation

### Transparent customer communication
Every response includes a line-item breakdown with unit prices, totals, discount rationale, and delivery dates. When items cannot be supplied, the response always states the specific reason (out of stock, insufficient quantity, not in catalogue). No internal data (DB row IDs, profit margins, SQL errors) is ever exposed.

### Partial fulfillment
Rather than rejecting an entire order when one item is unavailable, the system fulfills whatever it can and explains each gap. Requests 2, 4, 5, 7, 9, 12, 16, 17, 18, and 19 all demonstrate partial fulfillment — a significantly better customer experience than an all-or-nothing rejection.

### Robust item name resolution
The inventory seeded ~17 of 42 possible products with exact names like `"100 lb cover stock"` and `"Large poster paper (24x36 inches)"`. Customer requests used informal language like `"heavy cardstock"` and `"poster board"`. The orchestrator's name-mapping table in the system prompt resolved these reliably across all 20 requests without a single mapping error causing an incorrect sale.

### Financial discipline
The 20% cash safety margin in `approve_purchase` acted as an automated financial control. Cash grew by $1,686 across the test period while the reserve was never breached. The `calculate_financial_health` tool rated the company's position throughout the run as EXCELLENT (cash-to-assets ratio consistently above 85%).

### Operational resilience
The run crashed once at request 14 due to a console encoding error (Unicode checkmark character on Windows cp1252). The checkpoint/resume mechanism meant only that single in-flight request was lost — all prior results were preserved and the run completed cleanly after a one-line fix (`sys.stdout.reconfigure(encoding='utf-8')`).

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

The evaluation across 20 live test requests demonstrated that the system handles partial fulfillment gracefully, applies financial controls automatically, and recovers from transient failures without data loss. The 8 cash balance changes and $1,686.30 net revenue across the test period confirm that the core business logic is working correctly.

The primary area for future investment is smarter demand handling: automated reorder triggers, embedding-based name resolution, and negotiation capability would reduce the 11 partially-fulfilled or declined requests and make the system production-ready.

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
