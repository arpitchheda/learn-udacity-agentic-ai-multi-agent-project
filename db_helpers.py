"""
db_helpers.py — Shared database engine and all seven required helper functions.

All agent modules import from here. project_starter.py also imports from here
so that the helper functions remain accessible at the top-level namespace for
the test harness.
"""

import pandas as pd
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.sql import text
from datetime import datetime, timedelta
from typing import Dict, List, Union

# ---------------------------------------------------------------------------
# Database engine — single shared instance used by all helper functions
# ---------------------------------------------------------------------------
_DB_PATH = Path(__file__).parent / "munder_difflin.db"
db_engine = create_engine(f"sqlite:///{_DB_PATH}")


# ---------------------------------------------------------------------------
# Helper 1 — get_all_inventory
# ---------------------------------------------------------------------------
def get_all_inventory(as_of_date: str) -> Dict[str, int]:
    """
    Retrieve a snapshot of available inventory as of a specific date.

    Calculates net quantity per item (stock_orders minus sales) up to and
    including as_of_date. Only items with positive net stock are returned.

    Args:
        as_of_date: ISO-formatted date string (YYYY-MM-DD).

    Returns:
        Dict mapping item_name → current stock quantity.
    """
    query = """
        SELECT
            item_name,
            SUM(CASE
                WHEN transaction_type = 'stock_orders' THEN units
                WHEN transaction_type = 'sales'        THEN -units
                ELSE 0
            END) AS stock
        FROM transactions
        WHERE item_name IS NOT NULL
          AND transaction_date <= :as_of_date
        GROUP BY item_name
        HAVING stock > 0
    """
    result = pd.read_sql(query, db_engine, params={"as_of_date": as_of_date})
    return dict(zip(result["item_name"], result["stock"]))


# ---------------------------------------------------------------------------
# Helper 2 — get_stock_level
# ---------------------------------------------------------------------------
def get_stock_level(item_name: str, as_of_date: Union[str, datetime]) -> pd.DataFrame:
    """
    Retrieve the net stock level for a single item as of a given date.

    Args:
        item_name:   Exact name of the inventory item.
        as_of_date:  Cutoff date (inclusive), ISO string or datetime.

    Returns:
        Single-row DataFrame with columns 'item_name' and 'current_stock'.
    """
    if isinstance(as_of_date, datetime):
        as_of_date = as_of_date.isoformat()

    query = """
        SELECT
            item_name,
            COALESCE(SUM(CASE
                WHEN transaction_type = 'stock_orders' THEN units
                WHEN transaction_type = 'sales'        THEN -units
                ELSE 0
            END), 0) AS current_stock
        FROM transactions
        WHERE item_name = :item_name
          AND transaction_date <= :as_of_date
    """
    return pd.read_sql(query, db_engine,
                       params={"item_name": item_name, "as_of_date": as_of_date})


# ---------------------------------------------------------------------------
# Helper 3 — get_supplier_delivery_date
# ---------------------------------------------------------------------------
def get_supplier_delivery_date(input_date_str: str, quantity: int) -> str:
    """
    Estimate supplier delivery date based on order quantity and start date.

    Lead times:
        ≤10 units   → same day (0 days)
        11–100      → +1 day
        101–1 000   → +4 days
        >1 000      → +7 days

    Args:
        input_date_str: Starting date in ISO format (YYYY-MM-DD).
        quantity:       Number of units ordered.

    Returns:
        Estimated delivery date as 'YYYY-MM-DD'.
    """
    try:
        base_date = datetime.fromisoformat(input_date_str.split("T")[0])
    except (ValueError, TypeError):
        base_date = datetime.now()

    if quantity <= 10:
        days = 0
    elif quantity <= 100:
        days = 1
    elif quantity <= 1000:
        days = 4
    else:
        days = 7

    return (base_date + timedelta(days=days)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Helper 4 — create_transaction
# ---------------------------------------------------------------------------
def create_transaction(
    item_name: str,
    transaction_type: str,
    quantity: int,
    price: float,
    date: Union[str, datetime],
) -> int:
    """
    Insert one row into the transactions table.

    Args:
        item_name:        Name of the item involved.
        transaction_type: Exactly 'stock_orders' or 'sales'.
        quantity:         Number of units.
        price:            Total monetary value of the transaction.
        date:             Transaction date (ISO string or datetime).

    Returns:
        Integer row ID of the newly inserted record.

    Raises:
        ValueError: If transaction_type is not 'stock_orders' or 'sales'.
    """
    date_str = date.isoformat() if isinstance(date, datetime) else date

    if transaction_type not in {"stock_orders", "sales"}:
        raise ValueError("transaction_type must be 'stock_orders' or 'sales'")

    row = pd.DataFrame([{
        "item_name": item_name,
        "transaction_type": transaction_type,
        "units": quantity,
        "price": price,
        "transaction_date": date_str,
    }])
    row.to_sql("transactions", db_engine, if_exists="append", index=False)

    result = pd.read_sql("SELECT last_insert_rowid() AS id", db_engine)
    return int(result.iloc[0]["id"])


# ---------------------------------------------------------------------------
# Helper 5 — get_cash_balance
# ---------------------------------------------------------------------------
def get_cash_balance(as_of_date: Union[str, datetime]) -> float:
    """
    Calculate the net cash balance as of a given date.

    Balance = total sales revenue − total stock purchase costs.

    Args:
        as_of_date: Cutoff date (inclusive), ISO string or datetime.

    Returns:
        Float cash balance in dollars. Returns 0.0 on error.
    """
    try:
        if isinstance(as_of_date, datetime):
            as_of_date = as_of_date.isoformat()

        txns = pd.read_sql(
            "SELECT * FROM transactions WHERE transaction_date <= :d",
            db_engine,
            params={"d": as_of_date},
        )
        if txns.empty:
            return 0.0

        sales     = txns.loc[txns["transaction_type"] == "sales",        "price"].sum()
        purchases = txns.loc[txns["transaction_type"] == "stock_orders", "price"].sum()
        return float(sales - purchases)

    except Exception as exc:
        print(f"[get_cash_balance] Error: {exc}")
        return 0.0


# ---------------------------------------------------------------------------
# Helper 6 — generate_financial_report
# ---------------------------------------------------------------------------
def generate_financial_report(as_of_date: Union[str, datetime]) -> Dict:
    """
    Generate a complete financial snapshot for the business.

    Args:
        as_of_date: Report date (inclusive), ISO string or datetime.

    Returns:
        Dict with keys: as_of_date, cash_balance, inventory_value,
        total_assets, inventory_summary (list), top_selling_products (list).
    """
    if isinstance(as_of_date, datetime):
        as_of_date = as_of_date.isoformat()

    cash = get_cash_balance(as_of_date)
    inventory_df = pd.read_sql("SELECT * FROM inventory", db_engine)

    inventory_value = 0.0
    inventory_summary = []
    for _, item in inventory_df.iterrows():
        stock = get_stock_level(item["item_name"], as_of_date)["current_stock"].iloc[0]
        value = stock * item["unit_price"]
        inventory_value += value
        inventory_summary.append({
            "item_name":  item["item_name"],
            "stock":      stock,
            "unit_price": item["unit_price"],
            "value":      value,
        })

    top_sales = pd.read_sql(
        """
        SELECT item_name,
               SUM(units) AS total_units,
               SUM(price) AS total_revenue
        FROM   transactions
        WHERE  transaction_type = 'sales'
          AND  transaction_date <= :d
        GROUP  BY item_name
        ORDER  BY total_revenue DESC
        LIMIT  5
        """,
        db_engine,
        params={"d": as_of_date},
    )

    return {
        "as_of_date":           as_of_date,
        "cash_balance":         cash,
        "inventory_value":      inventory_value,
        "total_assets":         cash + inventory_value,
        "inventory_summary":    inventory_summary,
        "top_selling_products": top_sales.to_dict(orient="records"),
    }


# ---------------------------------------------------------------------------
# Helper 7 — search_quote_history
# ---------------------------------------------------------------------------
def search_quote_history(search_terms: List[str], limit: int = 5) -> List[Dict]:
    """
    Search historical quotes by keyword, matching customer requests and
    quote explanations.

    Args:
        search_terms: List of keywords to search for (case-insensitive).
        limit:        Maximum number of results to return.

    Returns:
        List of dicts with keys: original_request, total_amount,
        quote_explanation, job_type, order_size, event_type, order_date.
    """
    conditions, params = [], {}
    for i, term in enumerate(search_terms):
        key = f"term_{i}"
        conditions.append(
            f"(LOWER(qr.response) LIKE :{key} OR LOWER(q.quote_explanation) LIKE :{key})"
        )
        params[key] = f"%{term.lower()}%"

    where = " AND ".join(conditions) if conditions else "1=1"
    query = f"""
        SELECT qr.response        AS original_request,
               q.total_amount,
               q.quote_explanation,
               q.job_type,
               q.order_size,
               q.event_type,
               q.order_date
        FROM   quotes q
        JOIN   quote_requests qr ON q.request_id = qr.id
        WHERE  {where}
        ORDER  BY q.order_date DESC
        LIMIT  {limit}
    """
    with db_engine.connect() as conn:
        rows = conn.execute(text(query), params)
        return [dict(r._mapping) for r in rows]
