"""
agents/quoting_agent.py — Quoting specialist agent.

Responsibilities:
    - Search historical quotes to calibrate pricing.
    - Estimate supplier delivery dates.
    - Look up unit prices for specific inventory items.

Discount tiers (unit-count based for precision):
    0 – 500 units   →  0 % discount
    501 – 1 000     → 10 % discount
    1 001+          → 15 % discount
"""

import pandas as pd
from smolagents import ToolCallingAgent, tool
from smolagents.monitoring import LogLevel

from db_helpers import db_engine, search_quote_history, get_supplier_delivery_date
from agents.model_config import llm_model


# ---------------------------------------------------------------------------
# Tool 1 — search_quote_history_tool
# Uses: search_quote_history()
# ---------------------------------------------------------------------------
@tool
def search_quote_history_tool(search_terms_str: str) -> str:
    """
    Search historical quotes for comparable past orders to guide pricing.

    Filters out invalid records where total_amount equals -1 (parse errors).

    Args:
        search_terms_str: Comma-separated keywords, e.g. "glossy paper, ceremony".

    Returns:
        Formatted string of matching historical quotes with amounts and metadata.
    """
    terms = [t.strip() for t in search_terms_str.split(",") if t.strip()]
    results = search_quote_history(terms, limit=5)

    # Exclude rows with placeholder/invalid amounts
    valid = [r for r in results if r.get("total_amount") != -1]
    if not valid:
        return "No relevant historical quotes found for these search terms."

    lines = ["Historical quote matches:"]
    for i, r in enumerate(valid, 1):
        lines.append(
            f"\n[{i}] Request: {r.get('original_request', 'N/A')}\n"
            f"    Amount: ${r.get('total_amount', 0):.2f} | "
            f"Job: {r.get('job_type', 'N/A')} | "
            f"Size: {r.get('order_size', 'N/A')} | "
            f"Event: {r.get('event_type', 'N/A')}\n"
            f"    Explanation: {r.get('quote_explanation', 'N/A')}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 2 — estimate_delivery
# Uses: get_supplier_delivery_date()
# ---------------------------------------------------------------------------
@tool
def estimate_delivery(start_date: str, quantity: int) -> str:
    """
    Estimate the supplier delivery date for an order.

    Lead times scale with quantity:
        ≤10 units → same day | 11-100 → +1 day | 101-1000 → +4 days | >1000 → +7 days

    Args:
        start_date: Order date in ISO format (YYYY-MM-DD).
        quantity:   Total units in the order.

    Returns:
        String stating the estimated delivery date.
    """
    delivery = get_supplier_delivery_date(start_date, quantity)
    return f"Estimated delivery for {quantity} units ordered on {start_date}: {delivery}"


# ---------------------------------------------------------------------------
# Tool 3 — get_item_unit_prices
# Uses: db_engine (inventory table lookup — no dedicated helper for this)
# ---------------------------------------------------------------------------
@tool
def get_item_unit_prices(item_names_str: str) -> str:
    """
    Look up unit prices for one or more inventory items by their exact names.

    Args:
        item_names_str: Comma-separated exact item names from the inventory.

    Returns:
        Formatted string listing each item name and its unit price.
    """
    names = [n.strip() for n in item_names_str.split(",") if n.strip()]
    if not names:
        return "No item names provided."

    prices_df = pd.read_sql("SELECT item_name, unit_price FROM inventory", db_engine)
    price_map = dict(zip(prices_df["item_name"], prices_df["unit_price"]))

    lines = ["Unit prices:"]
    for name in names:
        price = price_map.get(name)
        if price is not None:
            lines.append(f"  - {name}: ${price:.4f} per unit")
        else:
            lines.append(f"  - {name}: not found in inventory")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent definition
# max_steps=6 — needs at most 3 tool calls (history + delivery + prices)
# ---------------------------------------------------------------------------
quoting_agent = ToolCallingAgent(
    tools=[search_quote_history_tool, estimate_delivery, get_item_unit_prices],
    model=llm_model,
    name="quoting_agent",
    description=(
        "Handles pricing and delivery estimates. "
        "Call this agent to: (1) search historical quotes for comparable orders, "
        "(2) estimate a delivery date given a start date and quantity, "
        "(3) look up unit prices for specific items. "
        "Discount tiers: 0-500 units=0%, 501-1000=10%, 1001+=15%."
    ),
    max_steps=6,
    verbosity_level=LogLevel.ERROR,
)
