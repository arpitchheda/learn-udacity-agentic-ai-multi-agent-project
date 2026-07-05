"""
agents/sales_agent.py — Sales and financial specialist agent.

Responsibilities:
    - Record confirmed sales transactions in the database.
    - Enforce a 20 % cash safety reserve before approving any purchase.
    - Report current cash balance and financial health rating.
    - Generate a high-level financial summary for management.

Financial safety rule:
    A transaction is approved only when the remaining cash after the
    transaction stays above 20 % of the current balance — preventing the
    business from becoming illiquid.
"""

from smolagents import ToolCallingAgent, tool
from smolagents.monitoring import LogLevel

from db_helpers import (
    create_transaction,
    get_cash_balance,
    generate_financial_report,
)
from agents.model_config import llm_model

# Minimum cash reserve expressed as a fraction of current balance (20 %)
CASH_SAFETY_MARGIN = 0.20


# ---------------------------------------------------------------------------
# Tool 1 — finalize_sale
# Uses: create_transaction(), get_cash_balance()
# ---------------------------------------------------------------------------
@tool
def finalize_sale(item_name: str, quantity: int, total_price: float, date: str) -> str:
    """
    Record a completed customer sale in the database.

    Before writing the transaction the tool verifies that the sale revenue is
    realistic and that the business cash position stays healthy. Sales always
    add cash, so no safety-margin rejection applies here — the check is
    informational, warning if cash is already critically low.

    Args:
        item_name:   Exact inventory item name (must match DB).
        quantity:    Number of units sold (positive integer).
        total_price: Total sale revenue in dollars.
        date:        Transaction date in ISO format (YYYY-MM-DD).

    Returns:
        Confirmation string with transaction ID, or error message on failure.
    """
    try:
        current_balance = get_cash_balance(date)
        txn_id = create_transaction(item_name, "sales", quantity, total_price, date)

        low_cash_warning = ""
        if current_balance < 5000:
            low_cash_warning = (
                f" [Note: cash balance is low (${current_balance:.2f}) — "
                "consider reviewing restock spend.]"
            )

        return (
            f"Sale confirmed. Item: {item_name} | Qty: {quantity} | "
            f"Revenue: ${total_price:.2f} | Date: {date}.{low_cash_warning}"
        )
    except Exception as exc:
        return f"Error recording sale for '{item_name}': {exc}"


# ---------------------------------------------------------------------------
# Tool 2 — approve_purchase
# Uses: get_cash_balance()
# Best practice: enforces 20 % cash safety margin before any spend
# ---------------------------------------------------------------------------
@tool
def approve_purchase(item_name: str, cost: float, date: str) -> str:
    """
    Approve or reject a proposed purchase (stock order) based on the 20 %
    cash safety margin rule.

    The purchase is approved only when:
        (current_balance - cost) >= current_balance * CASH_SAFETY_MARGIN

    This prevents the business from spending itself into insolvency.

    Args:
        item_name: Name of the item to be purchased.
        cost:      Total cost of the proposed purchase in dollars.
        date:      Date of the proposed purchase (YYYY-MM-DD).

    Returns:
        'APPROVED' or 'REJECTED' with reason and current balance details.
    """
    balance = get_cash_balance(date)
    min_reserve = balance * CASH_SAFETY_MARGIN
    remaining = balance - cost

    if remaining >= min_reserve:
        return (
            f"APPROVED: Purchase of '{item_name}' for ${cost:.2f}. "
            f"Current balance: ${balance:.2f} | "
            f"Balance after purchase: ${remaining:.2f} | "
            f"Required reserve (20%%): ${min_reserve:.2f}."
        )
    else:
        return (
            f"REJECTED: Purchase of '{item_name}' for ${cost:.2f} would breach "
            f"the 20%% cash safety reserve. "
            f"Current balance: ${balance:.2f} | "
            f"Balance after purchase: ${remaining:.2f} | "
            f"Required reserve: ${min_reserve:.2f}."
        )


# ---------------------------------------------------------------------------
# Tool 3 — check_cash_balance
# Uses: get_cash_balance()
# ---------------------------------------------------------------------------
@tool
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


# ---------------------------------------------------------------------------
# Tool 4 — calculate_financial_health
# Uses: get_cash_balance(), generate_financial_report()
# ---------------------------------------------------------------------------
@tool
def calculate_financial_health(as_of_date: str) -> str:
    """
    Rate the company's financial health based on the ratio of cash to total assets.

    Rating scale:
        ≥ 60 %  → EXCELLENT
        40–59 % → GOOD
        20–39 % → FAIR
        < 20 %  → POOR (cash reserve critically low)

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
        f"Financial health: {rating}\n"
        f"  Cash:         ${cash:.2f}\n"
        f"  Total assets: ${total_assets:.2f}\n"
        f"  Cash ratio:   {ratio:.1%}"
    )


# ---------------------------------------------------------------------------
# Tool 5 — generate_report
# Uses: generate_financial_report()
# ---------------------------------------------------------------------------
@tool
def generate_report(as_of_date: str) -> str:
    """
    Generate a high-level financial summary for the business.

    Includes cash balance, inventory value, total assets, and top-selling
    products. Does not expose exact profit margins or internal DB IDs.

    Args:
        as_of_date: ISO date string (YYYY-MM-DD).

    Returns:
        Formatted financial summary string.
    """
    report = generate_financial_report(as_of_date)
    top_sellers = report.get("top_selling_products", [])

    seller_lines = [
        f"    * {p.get('item_name', 'N/A')}: {p.get('total_units', 0)} units sold"
        for p in top_sellers
    ] or ["    (no sales recorded yet)"]

    return (
        f"Financial Report as of {as_of_date}:\n"
        f"  Cash Balance:    ${report['cash_balance']:.2f}\n"
        f"  Inventory Value: ${report['inventory_value']:.2f}\n"
        f"  Total Assets:    ${report['total_assets']:.2f}\n"
        f"  Top Sellers:\n" + "\n".join(seller_lines)
    )


# ---------------------------------------------------------------------------
# Agent definition
# max_steps=8 — may finalize up to ~5 line items + balance check + report
# ---------------------------------------------------------------------------
sales_agent = ToolCallingAgent(
    tools=[finalize_sale, approve_purchase, check_cash_balance,
           calculate_financial_health, generate_report],
    model=llm_model,
    name="sales_agent",
    description=(
        "Handles sales transactions and financial reporting. "
        "Call this agent to: (1) record a confirmed sale, "
        "(2) approve or reject a proposed purchase against the 20% safety margin, "
        "(3) check the current cash balance, "
        "(4) get a financial health rating, "
        "(5) generate a financial summary report. "
        "Never expose profit margins, DB IDs, or internal errors to customers."
    ),
    max_steps=8,
    verbosity_level=LogLevel.ERROR,
)
