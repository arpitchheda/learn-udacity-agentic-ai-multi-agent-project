"""
agents/inventory_agent.py — Inventory specialist agent.

Responsibilities:
    - Return the full inventory snapshot with stock quantities and unit prices.
    - Return the stock level for a single named item.

Tools use helper functions from db_helpers; no SQL or business logic lives here.
"""

import pandas as pd
from smolagents import ToolCallingAgent, tool
from smolagents.monitoring import LogLevel

from db_helpers import db_engine, get_all_inventory, get_stock_level
from agents.model_config import llm_model


# ---------------------------------------------------------------------------
# Tool 1 — check_all_inventory
# Uses: get_all_inventory()
# ---------------------------------------------------------------------------
@tool
def check_all_inventory(as_of_date: str) -> str:
    """
    Return every available inventory item with its stock quantity and unit price.

    Args:
        as_of_date: ISO date string (YYYY-MM-DD) for the inventory snapshot.

    Returns:
        Formatted string listing item name, available units, and unit price.
    """
    stock_dict = get_all_inventory(as_of_date)

    # Join unit prices from the inventory reference table
    prices_df = pd.read_sql("SELECT item_name, unit_price FROM inventory", db_engine)
    price_map = dict(zip(prices_df["item_name"], prices_df["unit_price"]))

    if not stock_dict:
        return f"No inventory available as of {as_of_date}."

    lines = [f"Inventory as of {as_of_date}:"]
    for name, qty in sorted(stock_dict.items()):
        price = price_map.get(name, "N/A")
        price_str = f"${price:.2f}" if isinstance(price, (int, float)) else price
        lines.append(f"  - {name}: {int(qty)} units @ {price_str} each")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 2 — check_stock_level
# Uses: get_stock_level()
# ---------------------------------------------------------------------------
@tool
def check_stock_level(item_name: str, as_of_date: str) -> str:
    """
    Return the current stock level for one specific inventory item.

    Args:
        item_name:   Exact item name as it appears in the database.
        as_of_date:  ISO date string (YYYY-MM-DD).

    Returns:
        Formatted string with the item name and its current stock count.
    """
    df = get_stock_level(item_name, as_of_date)
    if df.empty:
        return f"No stock information found for '{item_name}'."
    stock = int(df["current_stock"].iloc[0])
    return f"'{item_name}' stock as of {as_of_date}: {stock} units"


# ---------------------------------------------------------------------------
# Agent definition
# max_steps=5 — inventory checks need at most 2 tool calls; cap prevents loops
# ---------------------------------------------------------------------------
inventory_agent = ToolCallingAgent(
    tools=[check_all_inventory, check_stock_level],
    model=llm_model,
    name="inventory_agent",
    description=(
        "Handles inventory queries. "
        "Call this agent to: (1) get the full list of available items with "
        "stock quantities and unit prices, or (2) check the stock level for "
        "a single specific item. Always pass an ISO date (YYYY-MM-DD)."
    ),
    max_steps=5,
    verbosity_level=LogLevel.ERROR,
)
