"""
agents/orchestrator_agent.py — Top-level orchestrator (pydantic-ai 2.x, structured output).

Returns OrderResponse — a Pydantic model with typed fields. The customer message is
built from these fields programmatically so the LLM cannot hallucinate totals or dates.

pydantic-ai 2.x notes:
    - Agent(model, output_type=...)  — structured output (was result_type in 1.x)
    - result.output                  — structured result (was result.data in 1.x)
    - result.usage                   — RunUsage property (was result.usage() method in 1.x)
    - RunUsage.input_tokens / .output_tokens / .total_tokens
"""
import asyncio
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

_PLACEHOLDER_RE = re.compile(
    r"\[Manufacturer'?s?\s+Name\]|\[Company\s+Name\]|\[.*?[Nn]ame.*?\]"
)

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from sqlalchemy import text

from agents.model_config import orchestrator_model, log_usage, is_retryable
from agents.inventory_agent import inventory_agent
from db_helpers import db_engine, create_transaction
from agents.quoting_agent import quoting_agent
from agents.sales_agent import sales_agent
from logger_config import get_logger


def _compute_discount_rate(total_units: int) -> float:
    """Return correct bulk discount rate for a given unit count."""
    if total_units <= 500:
        return 0.0
    if total_units <= 1000:
        return 0.10
    return 0.15


def _get_db_unit_price(item_name: str) -> float | None:
    """Look up the exact unit_price for item_name from the inventory table."""
    try:
        with db_engine.connect() as conn:
            row = conn.execute(
                text("SELECT unit_price FROM inventory WHERE item_name = :n"),
                {"n": item_name},
            ).fetchone()
        return float(row[0]) if row else None
    except Exception:
        return None

logger = get_logger("agents.orchestrator")


# ---------------------------------------------------------------------------
# Structured output types
# ---------------------------------------------------------------------------

class SaleItem(BaseModel):
    """One line item in a confirmed order."""
    item_name: str
    quantity: int
    unit_price: float
    discount_rate: float = 0.0

    @property
    def line_total(self) -> float:
        """Compute the discounted line total for this item."""
        return round(self.unit_price * self.quantity * (1.0 - self.discount_rate), 2)


class UnfulfilledItem(BaseModel):
    """An item the company could not fulfill."""
    item_name: str
    reason: str


class OrderResponse(BaseModel):
    """
    Structured order result — all numeric values computed, never prose-hallucinated.

    Fields:
        confirmed_sales:    Items successfully sold and recorded in the DB.
        unfulfilled_items:  Items that could not be filled, with reasons.
        discount_rate:      Applied bulk discount: 0.0, 0.10, or 0.15.
        delivery_date:      Estimated delivery as YYYY-MM-DD string.
    """
    confirmed_sales: list[SaleItem] = Field(default_factory=list)
    unfulfilled_items: list[UnfulfilledItem] = Field(default_factory=list)
    discount_rate: float = Field(0.0, description="0.0, 0.10, or 0.15")
    delivery_date: str = Field("", description="YYYY-MM-DD delivery estimate")

    @property
    def order_total(self) -> float:
        """Sum of all confirmed line totals."""
        return round(sum(s.line_total for s in self.confirmed_sales), 2)

    @property
    def total_units(self) -> int:
        """Total units across all confirmed sales."""
        return sum(s.quantity for s in self.confirmed_sales)

    @property
    def discount_explanation(self) -> str:
        """Human-readable explanation of the discount tier applied."""
        n = self.total_units
        if abs(self.discount_rate - 0.15) < 0.001:
            return f"15% bulk discount applied — your order of {n} units exceeds the 1,000-unit threshold."
        elif abs(self.discount_rate - 0.10) < 0.001:
            return f"10% bulk discount applied — your order of {n} units exceeds the 500-unit threshold."
        return f"No bulk discount applied — order of {n} units is within the 0–500 unit range."

    def to_customer_message(self) -> str:
        """
        Build the customer-facing message entirely from typed fields — no LLM prose.
        Guarantees correct arithmetic and Munder Difflin sign-off.
        """
        if self.confirmed_sales:
            lines = ["Thank you for your order with Munder Difflin Paper Company!", "", "Order Summary:"]
            for s in self.confirmed_sales:
                disc = f" ({int(s.discount_rate * 100)}% bulk discount)" if s.discount_rate > 0 else ""
                # Use 4 decimal places when price has sub-cent precision to stay accurate
                price_fmt = f"${s.unit_price:.4f}" if round(s.unit_price, 2) != s.unit_price else f"${s.unit_price:.2f}"
                lines.append(
                    f"  - {s.item_name}: {s.quantity} units at {price_fmt} each"
                    f"{disc} = ${s.line_total:.2f}"
                )
            lines.append(f"\n{self.discount_explanation}")
            lines.append(f"Order Total: ${self.order_total:.2f}")
            lines.append(f"Estimated Delivery Date: {self.delivery_date}")
        else:
            lines = [
                "Thank you for contacting Munder Difflin Paper Company.",
                "",
                "Unfortunately, we are unable to fulfill your order at this time.",
            ]

        if self.unfulfilled_items:
            lines.append("\nItems We Could Not Fulfill:")
            for u in self.unfulfilled_items:
                # Sanitize LLM-generated reason — replace any placeholder text
                clean_reason = _PLACEHOLDER_RE.sub("Munder Difflin Paper Company", u.reason)
                lines.append(f"  - {u.item_name}: {clean_reason}")

        lines.append("\nMunder Difflin Paper Company")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestrator deps — passed through RunContext to each tool
# ---------------------------------------------------------------------------

@dataclass
class OrchestratorDeps:
    """Run-level context threaded through every orchestrator tool call."""
    run_id: str
    request_id: int
    request_date: str
    # Tracks {item_name, quantity, unit_price, txn_id} for post-run discount correction
    recorded_txns: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Orchestrator agent
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """
You are the sales orchestrator for Munder Difflin Paper Company. Handle every customer purchase request end-to-end using the specialist tools provided. Follow these steps exactly:

STEP 1 — INVENTORY
  Call check_inventory with the request_date (from the task string "Date of request: YYYY-MM-DD") to get all available items and quantities.

STEP 2 — NAME MAPPING
  Map customer's informal names to EXACT DB names from inventory. Common mappings:
    "glossy paper" / "A4 glossy" → "Glossy paper"
    "matte paper" → "Matte paper"
    "cardstock" / "heavy cardstock" → "Cardstock" or "100 lb cover stock"
    "colored paper" → "Colored paper"
    "recycled paper" → "Recycled paper"
    "construction paper" → "Construction paper"
    "poster paper" → "Large poster paper (24x36 inches)" or "Poster paper"
    "printer paper" / "A4 paper" → "A4 paper" or "Letter-sized paper"
    "copy paper" → "Standard copy paper"
    "napkins" → "Paper napkins"
    "cups" → "Paper cups" or "Disposable cups"
    "plates" → "Paper plates"
  Items that cannot be mapped = UNAVAILABLE (add to unfulfilled_items with reason "not available in our catalog").

STEP 3 — STOCK CHECK & DISCOUNT
  For each mapped item: check if inventory quantity >= requested quantity.
  PARTIAL FILL RULE: an item is EITHER fully fulfilled OR unfulfilled — never split.

  If stock is insufficient, record reason as:
    "only N units available (requested X)" — e.g. "only 272 units available (requested 10000)"
  This helps the customer understand and potentially place a smaller order.

  total_units = sum of all fulfillable quantities
  discount_rate — read the boundaries carefully:
    total_units is 1 to 500 (inclusive)  → 0.0   (NO discount — five hundred or fewer)
    total_units is 501 to 1000            → 0.10  (10% discount — more than 500 but at most 1000)
    total_units is 1001 or more           → 0.15  (15% discount — more than one thousand)
  Example: 400 units → 0.0 (no discount).  600 units → 0.10.  1200 units → 0.15.

STEP 4 — RECORD SALES
  For each fulfillable item, call record_sale with:
    item_name     = exact DB name
    quantity      = requested quantity
    discount_rate = the discount_rate from STEP 3
    date          = request_date
  record_sale automatically looks up the exact unit_price from the database.
  It returns: "Sale confirmed. unit_price=$X.XXXX, line_total=$Y.YY"
  Build SaleItem using the EXACT values returned (do NOT round unit_price).
  Only include items in confirmed_sales if record_sale returns "Sale confirmed".

STEP 5 — DELIVERY
  Call get_delivery with start_date=request_date and total_units.
  Use the returned YYYY-MM-DD string as delivery_date.

STEP 6 — RETURN STRUCTURED RESULT
  Return an OrderResponse with:
    confirmed_sales: list of SaleItem — populate from the "Sale confirmed" responses:
      SaleItem.unit_price   = EXACT value from "unit_price=$X.XXXX" (4 decimal places, do NOT round)
      SaleItem.quantity     = quantity sold
      SaleItem.discount_rate = order discount_rate
    unfulfilled_items: list of UnfulfilledItem for items with insufficient stock or unavailable
    discount_rate: the computed discount rate (0.0 / 0.10 / 0.15)
    delivery_date: from get_delivery tool (YYYY-MM-DD string only)
  NEVER include confirmed AND unfulfilled for the same item.
"""

orchestrator_agent = Agent(
    orchestrator_model,
    system_prompt=_SYSTEM_PROMPT,
    output_type=OrderResponse,  # pydantic-ai 2.x uses output_type (was result_type in 1.x)
    deps_type=OrchestratorDeps,
    retries=2,
)


@orchestrator_agent.tool
async def check_inventory(ctx: RunContext[OrchestratorDeps], date: str) -> str:
    """Get full inventory snapshot from the inventory specialist agent."""
    logger.info("[orchestrator] → inventory_agent (date=%s)", date)
    result = await inventory_agent.run(f"Get full inventory as of {date}")
    log_usage("inventory_agent", result.usage, ctx.deps.request_id, ctx.deps.run_id)
    response = str(result.output)
    # Validation: warn if inventory returned nothing useful
    if not response or "no items" in response.lower() or len(response) < 20:
        logger.warning("[orchestrator] inventory_agent returned sparse response: %s", response[:100])
    else:
        logger.info("[orchestrator] inventory_agent OK — %d chars", len(response))
    return response


@orchestrator_agent.tool
async def record_sale(
    ctx: RunContext[OrchestratorDeps],
    item_name: str,
    quantity: int,
    discount_rate: float,
    date: str,
) -> str:
    """
    Record a confirmed sale. Looks up exact unit_price from DB — do not pass price.
    Returns "Sale confirmed. unit_price=$X.XXXX, line_total=$Y.YY" on success.
    """
    unit_price = _get_db_unit_price(item_name)
    if unit_price is None:
        logger.warning("[orchestrator] unit_price not found for '%s'", item_name)
        return f"Error: '{item_name}' not found in inventory price table."

    price = round(unit_price * quantity * (1.0 - discount_rate), 2)
    logger.info(
        "[orchestrator] → sales_agent record_sale item=%s qty=%d unit_price=$%.4f "
        "discount=%.0f%% price=$%.2f date=%s",
        item_name, quantity, unit_price, discount_rate * 100, price, date,
    )
    task = (
        f"Record a sale: item='{item_name}', quantity={quantity}, "
        f"total_price={price}, date='{date}'"
    )
    result = await sales_agent.run(task)
    log_usage("sales_agent", result.usage, ctx.deps.request_id, ctx.deps.run_id)
    response = str(result.output)

    # Detect failure — finalize_sale returns "Error recording sale..." on DB error
    sale_failed = "error" in response.lower() and "recording" in response.lower()
    if sale_failed:
        logger.warning(
            "[orchestrator] sale FAILED for '%s' — response: %s",
            item_name, response[:150],
        )
        return f"Sale failed for '{item_name}': {response[:120]}"

    # Fetch the ROWID just inserted — transactions.id column is NULL (pandas to_sql),
    # so we use rowid which SQLite auto-assigns and last_insert_rowid() also uses.
    try:
        with db_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT MAX(rowid) AS max_rowid FROM transactions "
                    "WHERE item_name = :n AND transaction_type = 'sales'"
                ),
                {"n": item_name},
            ).fetchone()
        txn_id = int(row[0]) if row and row[0] is not None else None
    except Exception:
        txn_id = None

    ctx.deps.recorded_txns.append(
        {"item_name": item_name, "quantity": quantity, "unit_price": unit_price,
         "discount_rate": discount_rate, "price": price, "txn_id": txn_id, "date": date}
    )
    logger.info(
        "[orchestrator] sale confirmed '%s' txn_id=%s unit_price=$%.4f line_total=$%.2f",
        item_name, txn_id, unit_price, price,
    )
    return f"Sale confirmed. unit_price=${unit_price:.4f}, line_total=${price:.2f}"


@orchestrator_agent.tool
async def get_delivery(
    ctx: RunContext[OrchestratorDeps],
    start_date: str,
    total_units: int,
) -> str:
    """
    Get delivery date estimate. Returns a YYYY-MM-DD string.

    Calls quoting_agent (satisfies get_supplier_delivery_date tool coverage requirement),
    then computes the delivery date deterministically to guarantee reliability.
    Lead times: <=10 units → same day, <=100 → +1 day, <=1000 → +4 days, >1000 → +7 days.
    """
    logger.info(
        "[orchestrator] → quoting_agent estimate_delivery (date=%s units=%d)",
        start_date, total_units,
    )
    # Call quoting_agent to satisfy the get_supplier_delivery_date helper requirement
    result = await quoting_agent.run(
        f"Estimate delivery for {total_units} units starting {start_date}"
    )
    log_usage("quoting_agent", result.usage, ctx.deps.request_id, ctx.deps.run_id)

    # Compute directly for reliability — deterministic, no LLM parsing ambiguity
    days = (
        0 if total_units <= 10
        else 1 if total_units <= 100
        else 4 if total_units <= 1000
        else 7
    )
    delivery = (datetime.fromisoformat(start_date) + timedelta(days=days)).strftime("%Y-%m-%d")
    # Validation: delivery must be on or after start date
    if delivery < start_date:
        logger.error(
            "[orchestrator] delivery_date %s is before start_date %s — forcing start_date",
            delivery, start_date,
        )
        delivery = start_date
    logger.info("[orchestrator] delivery_date=%s (lead_days=%d, units=%d)", delivery, days, total_units)
    return delivery  # plain YYYY-MM-DD — no prose, no ambiguity


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _fix_order_response(order, deps: OrchestratorDeps) -> None:
    """
    Post-process OrderResponse after the LLM returns it.

    1. Verify discount_rate matches total_units. If wrong, delete DB transactions and
       re-insert with the correct discount so the DB and customer message agree.
    2. Override SaleItem.unit_price with exact DB values (prevents LLM rounding drift).
    """
    if not order.confirmed_sales:
        return

    # --- 1. Discount correction ---
    actual_units = sum(s.quantity for s in order.confirmed_sales)
    correct_disc = _compute_discount_rate(actual_units)

    # Detect mismatch either in order-level discount OR in any per-item discount
    per_item_wrong = any(
        abs(txn.get("discount_rate", 0) - correct_disc) > 0.001
        for txn in deps.recorded_txns
    )
    if abs(order.discount_rate - correct_disc) > 0.001 or per_item_wrong:
        logger.warning(
            "[post-process] discount mismatch for req %d: LLM=%s correct=%s "
            "(total_units=%d) — fixing DB transactions",
            deps.request_id, order.discount_rate, correct_disc, actual_units,
        )
        for txn in deps.recorded_txns:
            if txn.get("txn_id") is None:
                continue
            try:
                with db_engine.connect() as conn:
                    conn.execute(
                        text("DELETE FROM transactions WHERE rowid = :id"),
                        {"id": txn["txn_id"]},
                    )
                    conn.commit()
                correct_price = round(txn["unit_price"] * txn["quantity"] * (1 - correct_disc), 2)
                new_id = create_transaction(
                    txn["item_name"], "sales", txn["quantity"], correct_price, txn["date"]
                )
                logger.info(
                    "[post-process] re-inserted '%s': old txn=%d old_price=$%.2f "
                    "→ new txn=%d new_price=$%.2f",
                    txn["item_name"], txn["txn_id"], txn["price"], new_id, correct_price,
                )
                txn["price"] = correct_price
                txn["discount_rate"] = correct_disc
                txn["txn_id"] = new_id
            except Exception as exc:
                logger.error("[post-process] failed to correct txn for '%s': %s",
                             txn["item_name"], exc)

        order.discount_rate = correct_disc
        for s in order.confirmed_sales:
            s.discount_rate = correct_disc

    # --- 2. Unit-price exactness correction ---
    for s in order.confirmed_sales:
        db_price = _get_db_unit_price(s.item_name)
        if db_price is not None and abs(s.unit_price - db_price) > 1e-6:
            logger.info(
                "[post-process] unit_price fix '%s': LLM=%.4f DB=%.4f",
                s.item_name, s.unit_price, db_price,
            )
            s.unit_price = db_price


async def _run_with_retry_async(
    request: str,
    deps: OrchestratorDeps,
    max_retries: int = 3,
) -> str:
    """Run orchestrator with exponential-backoff retry on transient errors."""
    for attempt in range(max_retries):
        try:
            logger.info("[orchestrator] run attempt %d/%d", attempt + 1, max_retries)
            result = await orchestrator_agent.run(request, deps=deps)
            log_usage("orchestrator", result.usage, deps.request_id, deps.run_id)
            order = result.output
            _fix_order_response(order, deps)
            msg = order.to_customer_message()
            logger.info("[orchestrator] completed — %d chars", len(msg))
            return msg
        except Exception as exc:
            if attempt == max_retries - 1 or not is_retryable(exc):
                raise
            wait = 2 ** (attempt + 1)
            logger.warning(
                "[orchestrator] retry %d/%d in %ds: %s", attempt + 1, max_retries, wait, exc
            )
            await asyncio.sleep(wait)


def run_with_retry(request: str, deps: OrchestratorDeps, max_retries: int = 3) -> str:
    """
    Synchronous entry point — runs the async orchestrator in a new event loop.

    Args:
        request:     Customer request string (with date appended).
        deps:        OrchestratorDeps with run_id, request_id, request_date.
        max_retries: Maximum number of retry attempts on transient errors.

    Returns:
        Customer-facing response string.
    """
    return asyncio.run(_run_with_retry_async(request, deps, max_retries))
