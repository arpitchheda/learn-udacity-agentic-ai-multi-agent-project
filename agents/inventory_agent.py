"""
agents/inventory_agent.py — Inventory specialist agent (pydantic-ai).

Responsibilities:
    - Return the full inventory snapshot with stock quantities and unit prices.
    - Return the stock level for a single named item.

Tools use helper functions from db_helpers; no SQL or business logic lives here.
Helper functions used: get_all_inventory, get_stock_level
"""

import os
import sys
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pydantic_ai import Agent
from db_helpers import db_engine, get_all_inventory, get_stock_level
from agents.model_config import specialist_model
from logger_config import get_logger

logger = get_logger("agents.inventory")

inventory_agent = Agent(
    specialist_model,
    system_prompt=(
        "You are an inventory specialist for Munder Difflin Paper Company. "
        "Use your tools to check stock levels and return clear inventory summaries. "
        "Always include item names, quantities, and unit prices in your response."
    ),
)


@inventory_agent.tool_plain
def check_all_inventory(date: str) -> str:
    """
    Return every available inventory item with stock quantity and unit price.

    Args:
        date: ISO date string (YYYY-MM-DD) for the inventory snapshot.

    Returns:
        Formatted string listing item name, available units, and unit price.
    """
    logger.info("[inventory] check_all_inventory called — date=%s", date)
    stock_dict = get_all_inventory(date)

    # Join unit prices from the inventory reference table
    prices_df = pd.read_sql("SELECT item_name, unit_price FROM inventory", db_engine)
    price_map = dict(zip(prices_df["item_name"], prices_df["unit_price"]))

    if not stock_dict:
        logger.warning("[inventory] No inventory available as of %s", date)
        return f"No inventory available as of {date}."

    lines = [f"Inventory as of {date}:"]
    for name, qty in sorted(stock_dict.items()):
        price = price_map.get(name, "N/A")
        price_str = f"${price:.4f}" if isinstance(price, (int, float)) else price
        lines.append(f"  - {name}: {int(qty)} units @ {price_str} each")
    logger.info("[inventory] Returned %d items", len(stock_dict))
    return "\n".join(lines)


@inventory_agent.tool_plain
def check_stock_level(item_name: str, date: str) -> str:
    """
    Return the current stock level for one specific inventory item.

    Args:
        item_name: Exact item name as it appears in the database.
        date:      ISO date string (YYYY-MM-DD).

    Returns:
        Formatted string with the item name and its current stock count.
    """
    logger.info("[inventory] check_stock_level called — item=%s date=%s", item_name, date)
    df = get_stock_level(item_name, date)
    if df.empty:
        logger.warning("[inventory] No stock found for '%s'", item_name)
        return f"No stock information found for '{item_name}'."
    stock = int(df.iloc[0]["current_stock"])
    logger.info("[inventory] '%s' stock = %d units", item_name, stock)
    return f"'{item_name}' stock as of {date}: {stock} units"
