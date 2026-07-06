"""
agents/sales_agent.py — Sales and financial specialist agent (pydantic-ai).

Responsibilities:
    - Record confirmed sales transactions in the database.
    - Enforce a 20% cash safety reserve before approving any purchase.
    - Report current cash balance and financial health rating.
    - Generate a high-level financial summary for management.

Financial safety rule:
    A transaction is approved only when the remaining cash after the
    transaction stays above 20% of the current balance — preventing the
    business from becoming illiquid.

Helper functions used: create_transaction, get_cash_balance, generate_financial_report
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pydantic_ai import Agent
from db_helpers import create_transaction, get_cash_balance, generate_financial_report
from agents.model_config import specialist_model
from logger_config import get_logger

logger = get_logger("agents.sales")

# Minimum cash reserve expressed as a fraction of current balance (20%)
CASH_SAFETY_MARGIN = 0.20

sales_agent = Agent(
    specialist_model,
    system_prompt=(
        "You are a sales and finance specialist for Munder Difflin Paper Company. "
        "Record sales transactions and provide financial reporting. "
        "Never expose internal DB IDs, profit margins, or SQL errors to customers."
    ),
)


@sales_agent.tool_plain
def finalize_sale(item_name: str, quantity: int, total_price: float, date: str) -> str:
    """
    Record a completed customer sale in the database.

    Verifies that the cash balance increased after recording the sale.
    Sales always add cash, so no safety-margin rejection applies here.

    Args:
        item_name:   Exact inventory item name (must match DB).
        quantity:    Number of units sold (positive integer).
        total_price: Total sale revenue in dollars.
        date:        Transaction date in ISO format (YYYY-MM-DD).

    Returns:
        Confirmation string with transaction ID, or error message on failure.
    """
    logger.info("[sales] finalize_sale called — item=%s qty=%d price=$%.2f date=%s",
                item_name, quantity, total_price, date)
    try:
        balance_before = get_cash_balance(date)
        txn_id = create_transaction(item_name, "sales", quantity, total_price, date)
        balance_after = get_cash_balance(date)
        if balance_after > balance_before:
            increase = balance_after - balance_before
            logger.info("[sales] VERIFIED: cash +$%.2f (txn_id=%s)", increase, txn_id)
            return (
                f"Sale confirmed. Item: {item_name} | Qty: {quantity} | "
                f"Revenue: ${total_price:.2f} | Date: {date} | TxnID: {txn_id}"
            )
        else:
            logger.warning("[sales] VERIFICATION FAILED for %s", item_name)
            return (
                f"Sale confirmed but verification warning for {item_name}. "
                f"TxnID: {txn_id}"
            )
    except Exception as exc:
        logger.error("[sales] Failed to record sale for '%s': %s", item_name, exc)
        return f"Error recording sale for '{item_name}': {exc}"


@sales_agent.tool_plain
def approve_purchase(item_name: str, cost: float, date: str) -> str:
    """
    Approve or reject a proposed purchase based on the 20% cash safety margin rule.

    The purchase is approved only when:
        (current_balance - cost) >= current_balance * CASH_SAFETY_MARGIN

    Args:
        item_name: Name of the item to be purchased.
        cost:      Total cost of the proposed purchase in dollars.
        date:      Date of the proposed purchase (YYYY-MM-DD).

    Returns:
        'APPROVED' or 'REJECTED' with reason and current balance details.
    """
    logger.info("[sales] approve_purchase called — item=%s cost=$%.2f date=%s",
                item_name, cost, date)
    balance = get_cash_balance(date)
    min_reserve = balance * CASH_SAFETY_MARGIN
    remaining = balance - cost

    if remaining >= min_reserve:
        logger.info("[sales] Purchase APPROVED — balance=$%.2f remaining=$%.2f",
                    balance, remaining)
        return (
            f"APPROVED: '{item_name}' for ${cost:.2f}. "
            f"Balance after: ${remaining:.2f} (reserve: ${min_reserve:.2f})."
        )
    logger.warning(
        "[sales] Purchase REJECTED — balance=$%.2f would drop to $%.2f (min reserve=$%.2f)",
        balance, remaining, min_reserve,
    )
    return (
        f"REJECTED: '{item_name}' for ${cost:.2f} would breach "
        f"the 20% cash safety reserve. "
        f"Balance: ${balance:.2f}, after: ${remaining:.2f}."
    )


@sales_agent.tool_plain
def check_cash_balance(as_of_date: str) -> str:
    """
    Return the current cash balance as of the given date.

    Args:
        as_of_date: ISO date string (YYYY-MM-DD).

    Returns:
        Formatted string showing the cash balance in dollars.
    """
    balance = get_cash_balance(as_of_date)
    return f"Cash balance as of {as_of_date}: ${balance:.2f}"


@sales_agent.tool_plain
def calculate_financial_health(as_of_date: str) -> str:
    """
    Rate the company's financial health based on cash-to-total-assets ratio.

    Rating scale:
        >= 60%  → EXCELLENT
        40-59%  → GOOD
        20-39%  → FAIR
        < 20%   → POOR (cash reserve critically low)

    Args:
        as_of_date: ISO date string (YYYY-MM-DD).

    Returns:
        Rating string with cash ratio and supporting figures.
    """
    report = generate_financial_report(as_of_date)
    total_assets = report["total_assets"]
    cash = report["cash_balance"]

    if total_assets <= 0:
        return "Financial health: UNKNOWN (no asset data available)."

    ratio = cash / total_assets
    if ratio >= 0.60:
        rating = "EXCELLENT"
    elif ratio >= 0.40:
        rating = "GOOD"
    elif ratio >= 0.20:
        rating = "FAIR"
    else:
        rating = "POOR"

    return (
        f"Financial health: {rating} | Cash: ${cash:.2f} | "
        f"Total assets: ${total_assets:.2f} | Ratio: {ratio:.1%}"
    )


@sales_agent.tool_plain
def generate_report(as_of_date: str) -> str:
    """
    Generate a high-level financial summary for the business.

    Includes cash balance, inventory value, total assets, and top-selling products.
    Does not expose exact profit margins or internal DB IDs.

    Args:
        as_of_date: ISO date string (YYYY-MM-DD).

    Returns:
        Formatted financial summary string.
    """
    report = generate_financial_report(as_of_date)
    top_sellers = report.get("top_selling_products", [])

    seller_lines = (
        "\n".join(
            f"    * {p.get('item_name', 'N/A')}: {p.get('total_units', 0)} units sold"
            for p in top_sellers
        )
        or "    (no sales recorded yet)"
    )

    return (
        f"Financial Report as of {as_of_date}:\n"
        f"  Cash: ${report['cash_balance']:.2f} | "
        f"Inventory: ${report['inventory_value']:.2f} | "
        f"Assets: ${report['total_assets']:.2f}\n"
        f"  Top sellers:\n{seller_lines}"
    )
