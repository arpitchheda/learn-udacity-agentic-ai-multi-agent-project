"""
agents/quoting_agent.py — Quoting specialist agent (pydantic-ai).

Responsibilities:
    - Search historical quotes to calibrate pricing.
    - Estimate supplier delivery dates.
    - Look up unit prices for specific inventory items.

Discount tiers (unit-count based for precision):
    0 – 500 units   →  0 % discount
    501 – 1 000     → 10 % discount
    1 001+          → 15 % discount

Helper functions used: search_quote_history, get_supplier_delivery_date, db_engine (inventory query)
"""

import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pydantic_ai import Agent
from db_helpers import db_engine, search_quote_history, get_supplier_delivery_date
from agents.model_config import specialist_model
from logger_config import get_logger

logger = get_logger("agents.quoting")

quoting_agent = Agent(
    specialist_model,
    system_prompt=(
        "You are a quoting specialist for Munder Difflin Paper Company. "
        "Search historical quotes to guide pricing and estimate delivery dates. "
        "Discount tiers: 0-500 units=0%, 501-1000=10%, 1001+=15%."
    ),
)


@quoting_agent.tool_plain
def search_quote_history_tool(search_terms_str: str) -> str:
    """
    Search historical quotes for comparable past orders to guide pricing.

    Filters out invalid records where total_amount equals -1 (parse errors).

    Args:
        search_terms_str: Comma-separated keywords, e.g. "glossy paper, ceremony".

    Returns:
        Formatted string of matching historical quotes with amounts and metadata.
    """
    logger.info("[quoting] search_quote_history called — terms=%s", search_terms_str)
    terms = [t.strip() for t in search_terms_str.split(",") if t.strip()]
    results = search_quote_history(terms, limit=5)

    # Exclude rows with placeholder/invalid amounts
    valid = [r for r in results if r.get("total_amount") != -1]
    if not valid:
        logger.warning("[quoting] No valid historical quotes found for terms: %s", search_terms_str)
        return "No relevant historical quotes found for these search terms."

    logger.info("[quoting] Found %d valid historical quotes", len(valid))
    lines = ["Historical quote matches:"]
    for i, r in enumerate(valid, 1):
        lines.append(
            f"[{i}] {r.get('original_request', 'N/A')} | "
            f"${r.get('total_amount', 0):.2f} | {r.get('order_size', 'N/A')} | "
            f"{r.get('event_type', 'N/A')}\n    {r.get('quote_explanation', 'N/A')}"
        )
    return "\n".join(lines)


@quoting_agent.tool_plain
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
    logger.info("[quoting] estimate_delivery called — start_date=%s quantity=%d", start_date, quantity)
    delivery = get_supplier_delivery_date(start_date, quantity)
    logger.info("[quoting] Delivery estimate: %s", delivery)
    return f"Estimated delivery for {quantity} units ordered on {start_date}: {delivery}"


@quoting_agent.tool_plain
def get_item_unit_prices(item_names_str: str) -> str:
    """
    Look up unit prices for one or more inventory items by their exact names.

    Args:
        item_names_str: Comma-separated exact item names from the inventory.

    Returns:
        Formatted string listing each item name and its unit price.
    """
    logger.info("[quoting] get_item_unit_prices called — items=%s", item_names_str)
    names = [n.strip() for n in item_names_str.split(",") if n.strip()]
    if not names:
        return "No item names provided."

    prices_df = pd.read_sql("SELECT item_name, unit_price FROM inventory", db_engine)
    price_map = dict(zip(prices_df["item_name"], prices_df["unit_price"]))

    lines = ["Unit prices:"]
    for name in names:
        price = price_map.get(name)
        if price is not None:
            lines.append(f"  {name}: ${price:.4f} per unit")
        else:
            lines.append(f"  {name}: not found in inventory")
    return "\n".join(lines)
