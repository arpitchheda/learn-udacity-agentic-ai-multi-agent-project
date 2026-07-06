"""
project_starter.py — Munder Difflin Paper Company, Multi-Agent System.

Entry point for running the evaluation harness.  All agent logic lives in
the agents/ package; all database helpers live in db_helpers.py.  This file
owns only:
    - The paper product catalogue (paper_supplies)
    - generate_sample_inventory() — random inventory subset generator
    - init_database()             — database setup / reset
    - run_test_scenarios()        — evaluation loop with resume / retry support
"""

import ast
import logging
import os
import shutil
import sys
import time
from pathlib import Path

# Force UTF-8 output so Unicode characters in agent responses don't crash on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from logger_config import setup_logging, get_logger

# Initialise logging before anything else — DEBUG to file, INFO to console
setup_logging(log_file="run_output.log", level=logging.DEBUG)
logger = get_logger("project_starter")

import pandas as pd
import numpy as np
from datetime import datetime
from typing import Union
from sqlalchemy import Engine

# ---------------------------------------------------------------------------
# Re-export db_engine and all helper functions so the test harness can use
# them directly (e.g. generate_financial_report, get_cash_balance, etc.)
# ---------------------------------------------------------------------------
from db_helpers import (
    db_engine,
    get_all_inventory,
    get_stock_level,
    get_supplier_delivery_date,
    create_transaction,
    get_cash_balance,
    generate_financial_report,
    search_quote_history,
)

# ---------------------------------------------------------------------------
# Agent package — orchestrator + retry wrapper
# ---------------------------------------------------------------------------
from agents import run_with_retry, OrchestratorDeps
from agents.model_config import set_request_context, set_run_id, TOKEN_CSV

# ---------------------------------------------------------------------------
# Paper product catalogue — full list of 42 items that Munder Difflin stocks
# ---------------------------------------------------------------------------
paper_supplies = [
    # Paper Types (priced per sheet)
    {"item_name": "A4 paper",                              "category": "paper",        "unit_price": 0.05},
    {"item_name": "Letter-sized paper",                    "category": "paper",        "unit_price": 0.06},
    {"item_name": "Cardstock",                             "category": "paper",        "unit_price": 0.15},
    {"item_name": "Colored paper",                         "category": "paper",        "unit_price": 0.10},
    {"item_name": "Glossy paper",                          "category": "paper",        "unit_price": 0.20},
    {"item_name": "Matte paper",                           "category": "paper",        "unit_price": 0.18},
    {"item_name": "Recycled paper",                        "category": "paper",        "unit_price": 0.08},
    {"item_name": "Eco-friendly paper",                    "category": "paper",        "unit_price": 0.12},
    {"item_name": "Poster paper",                          "category": "paper",        "unit_price": 0.25},
    {"item_name": "Banner paper",                          "category": "paper",        "unit_price": 0.30},
    {"item_name": "Kraft paper",                           "category": "paper",        "unit_price": 0.10},
    {"item_name": "Construction paper",                    "category": "paper",        "unit_price": 0.07},
    {"item_name": "Wrapping paper",                        "category": "paper",        "unit_price": 0.15},
    {"item_name": "Glitter paper",                         "category": "paper",        "unit_price": 0.22},
    {"item_name": "Decorative paper",                      "category": "paper",        "unit_price": 0.18},
    {"item_name": "Letterhead paper",                      "category": "paper",        "unit_price": 0.12},
    {"item_name": "Legal-size paper",                      "category": "paper",        "unit_price": 0.08},
    {"item_name": "Crepe paper",                           "category": "paper",        "unit_price": 0.05},
    {"item_name": "Photo paper",                           "category": "paper",        "unit_price": 0.25},
    {"item_name": "Uncoated paper",                        "category": "paper",        "unit_price": 0.06},
    {"item_name": "Butcher paper",                         "category": "paper",        "unit_price": 0.10},
    {"item_name": "Heavyweight paper",                     "category": "paper",        "unit_price": 0.20},
    {"item_name": "Standard copy paper",                   "category": "paper",        "unit_price": 0.04},
    {"item_name": "Bright-colored paper",                  "category": "paper",        "unit_price": 0.12},
    {"item_name": "Patterned paper",                       "category": "paper",        "unit_price": 0.15},
    # Product Types (priced per unit)
    {"item_name": "Paper plates",                          "category": "product",      "unit_price": 0.10},
    {"item_name": "Paper cups",                            "category": "product",      "unit_price": 0.08},
    {"item_name": "Paper napkins",                         "category": "product",      "unit_price": 0.02},
    {"item_name": "Disposable cups",                       "category": "product",      "unit_price": 0.10},
    {"item_name": "Table covers",                          "category": "product",      "unit_price": 1.50},
    {"item_name": "Envelopes",                             "category": "product",      "unit_price": 0.05},
    {"item_name": "Sticky notes",                          "category": "product",      "unit_price": 0.03},
    {"item_name": "Notepads",                              "category": "product",      "unit_price": 2.00},
    {"item_name": "Invitation cards",                      "category": "product",      "unit_price": 0.50},
    {"item_name": "Flyers",                                "category": "product",      "unit_price": 0.15},
    {"item_name": "Party streamers",                       "category": "product",      "unit_price": 0.05},
    {"item_name": "Decorative adhesive tape (washi tape)", "category": "product",      "unit_price": 0.20},
    {"item_name": "Paper party bags",                      "category": "product",      "unit_price": 0.25},
    {"item_name": "Name tags with lanyards",               "category": "product",      "unit_price": 0.75},
    {"item_name": "Presentation folders",                  "category": "product",      "unit_price": 0.50},
    # Large-format items (priced per unit)
    {"item_name": "Large poster paper (24x36 inches)",     "category": "large_format", "unit_price": 1.00},
    {"item_name": "Rolls of banner paper (36-inch width)", "category": "large_format", "unit_price": 2.50},
    # Specialty papers
    {"item_name": "100 lb cover stock",                    "category": "specialty",    "unit_price": 0.50},
    {"item_name": "80 lb text paper",                      "category": "specialty",    "unit_price": 0.40},
    {"item_name": "250 gsm cardstock",                     "category": "specialty",    "unit_price": 0.30},
    {"item_name": "220 gsm poster paper",                  "category": "specialty",    "unit_price": 0.35},
]


# ---------------------------------------------------------------------------
# generate_sample_inventory
# ---------------------------------------------------------------------------
def generate_sample_inventory(
    paper_supplies: list,
    coverage: float = 0.4,
    seed: int = 137,
) -> pd.DataFrame:
    """
    Generate inventory for a random subset of items from the full catalogue.

    Selects exactly coverage × N items using the given seed (reproducible).
    Each selected item is assigned:
        - current_stock:   random integer between 200 and 800
        - min_stock_level: random integer between 50 and 150

    Args:
        paper_supplies: Full list of paper product dicts.
        coverage:       Fraction of items to include (default 0.4 → ~17 items).
        seed:           NumPy random seed for reproducibility (default 137).

    Returns:
        DataFrame with columns: item_name, category, unit_price,
        current_stock, min_stock_level.
    """
    np.random.seed(seed)
    num_items = int(len(paper_supplies) * coverage)
    selected = np.random.choice(range(len(paper_supplies)), size=num_items, replace=False)

    inventory = []
    for i in selected:
        item = paper_supplies[i]
        inventory.append({
            "item_name":       item["item_name"],
            "category":        item["category"],
            "unit_price":      item["unit_price"],
            "current_stock":   np.random.randint(200, 800),
            "min_stock_level": np.random.randint(50, 150),
        })
    return pd.DataFrame(inventory)


# ---------------------------------------------------------------------------
# init_database
# ---------------------------------------------------------------------------
def init_database(engine: Engine, seed: int = 137) -> Engine:
    """
    Create and seed all database tables from scratch.

    Tables created:
        transactions   — sales and stock-order log
        quote_requests — historical customer inquiries (from quote_requests.csv)
        quotes         — historical quote records (from quotes.csv)
        inventory      — current stock reference table

    The transactions table is seeded with:
        - One 'sales' row of $50,000 (starting cash balance)
        - One 'stock_orders' row per inventory item (initial stock purchase)

    Args:
        engine: SQLAlchemy engine pointing at the SQLite database.
        seed:   Random seed for inventory generation (default 137).

    Returns:
        The same engine after all tables have been created and seeded.
    """
    try:
        # 1. Create empty transactions table
        pd.DataFrame({
            "id": [], "item_name": [], "transaction_type": [],
            "units": [], "price": [], "transaction_date": [],
        }).to_sql("transactions", engine, if_exists="replace", index=False)

        initial_date = datetime(2025, 1, 1).isoformat()

        # 2. Load quote_requests
        qr_df = pd.read_csv("quote_requests.csv")
        qr_df["id"] = range(1, len(qr_df) + 1)
        qr_df.to_sql("quote_requests", engine, if_exists="replace", index=False)

        # 3. Load and transform quotes
        q_df = pd.read_csv("quotes.csv")
        q_df["request_id"] = range(1, len(q_df) + 1)
        q_df["order_date"] = initial_date
        if "request_metadata" in q_df.columns:
            q_df["request_metadata"] = q_df["request_metadata"].apply(
                lambda x: ast.literal_eval(x) if isinstance(x, str) else x
            )
            q_df["job_type"]   = q_df["request_metadata"].apply(lambda x: x.get("job_type", ""))
            q_df["order_size"] = q_df["request_metadata"].apply(lambda x: x.get("order_size", ""))
            q_df["event_type"] = q_df["request_metadata"].apply(lambda x: x.get("event_type", ""))
        q_df[["request_id", "total_amount", "quote_explanation",
              "order_date", "job_type", "order_size", "event_type"]
             ].to_sql("quotes", engine, if_exists="replace", index=False)

        # 4. Generate inventory and seed transactions
        inventory_df = generate_sample_inventory(paper_supplies, seed=seed)
        seed_txns = [
            # Starting cash balance (dummy sales entry)
            {"item_name": None, "transaction_type": "sales",
             "units": None, "price": 50000.0, "transaction_date": initial_date},
        ]
        for _, item in inventory_df.iterrows():
            seed_txns.append({
                "item_name":        item["item_name"],
                "transaction_type": "stock_orders",
                "units":            item["current_stock"],
                "price":            item["current_stock"] * item["unit_price"],
                "transaction_date": initial_date,
            })
        pd.DataFrame(seed_txns).to_sql("transactions", engine, if_exists="append", index=False)
        inventory_df.to_sql("inventory", engine, if_exists="replace", index=False)

        return engine

    except Exception as exc:
        logger.error("Error initializing database: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------
RESULTS_CSV    = "test_results.csv"
CHECKPOINT_DIR = Path("checkpoints")
_DB_PATH       = Path("munder_difflin.db")


# ---------------------------------------------------------------------------
# DB checkpoint helpers — snapshot the DB after each request so --request N
# can restore state without re-running every prior request.
# ---------------------------------------------------------------------------

def _save_checkpoint(request_id: int) -> None:
    """Copy the current DB to checkpoints/db_after_{N}.db."""
    CHECKPOINT_DIR.mkdir(exist_ok=True)
    dest = CHECKPOINT_DIR / f"db_after_{request_id}.db"
    shutil.copy2(_DB_PATH, dest)
    logger.debug("[checkpoint] Saved after request %d → %s", request_id, dest)


def _restore_checkpoint(request_id: int) -> bool:
    """Restore DB from snapshot taken after request_id. Returns True if found."""
    src = CHECKPOINT_DIR / f"db_after_{request_id}.db"
    if src.exists():
        shutil.copy2(src, _DB_PATH)
        logger.info("[checkpoint] Restored DB state from after request %d", request_id)
        return True
    logger.debug("[checkpoint] No checkpoint for after request %d", request_id)
    return False


def _load_completed_request_ids(results_csv: str) -> dict:
    """
    Read test_results.csv and return a dict of successfully completed rows.

    A row is 'completed' only when its response does NOT start with '[ERROR]'.
    Error rows are always eligible for retry on the next run.

    Args:
        results_csv: Path to the results CSV file.

    Returns:
        Dict mapping request_id (int) → result row dict for completed rows.
    """
    if not os.path.exists(results_csv):
        return {}
    try:
        df = pd.read_csv(results_csv)
        return {
            int(row["request_id"]): row.to_dict()
            for _, row in df.iterrows()
            if not str(row.get("response", "")).startswith("[ERROR]")
        }
    except Exception as exc:
        logger.warning("Could not read %s: %s. Starting fresh.", results_csv, exc)
        return {}


# ---------------------------------------------------------------------------
# run_test_scenarios — main evaluation harness
# ---------------------------------------------------------------------------
def run_test_scenarios(
    dry_run: bool = False,
    limit: int = None,
    force_rerun: bool = False,
    interactive: bool = False,
    target_request: int = None,
) -> list:
    """
    Process requests from quote_requests_sample.csv through the multi-agent system.

    Resume behaviour (default):
        Reads test_results.csv and skips any request that already has a
        successful response. Only failed or new requests are processed.

    Args:
        dry_run:         Process only the first pending request without writing CSV.
        limit:           Maximum number of pending requests to process this run.
        force_rerun:     Ignore existing results, reinit DB, run all 20 from scratch.
        target_request:  Run only up to request N.  If N has an existing result it
                         is deleted and re-run.  Prerequisites (1..N-1) are run first
                         if not already done, using DB checkpoints where available.

    Returns:
        List of result dicts (one per request) sorted by request_id.
    """
    # ------------------------------------------------------------------
    # Load input data
    # ------------------------------------------------------------------
    try:
        sample = pd.read_csv("quote_requests_sample.csv")
        sample["request_date"] = pd.to_datetime(
            sample["request_date"], format="%m/%d/%y", errors="coerce"
        )
        sample.dropna(subset=["request_date"], inplace=True)
        sample = sample.sort_values("request_date").reset_index(drop=True)
        sample["request_id"] = range(1, len(sample) + 1)
    except Exception as exc:
        logger.critical("Could not load quote_requests_sample.csv: %s", exc)
        return []

    # ------------------------------------------------------------------
    # Determine which requests still need processing
    # ------------------------------------------------------------------
    if target_request is not None:
        N = target_request
        logger.info("[--request %d] Single-request mode.", N)

        # Remove result for N (and any beyond N) from the CSV so we can re-run cleanly
        if os.path.exists(RESULTS_CSV):
            df_csv = pd.read_csv(RESULTS_CSV)
            df_keep = df_csv[df_csv["request_id"].astype(int) < N]
            removed = len(df_csv) - len(df_keep)
            if removed:
                logger.info("[--request %d] Removed %d result row(s) >= %d from CSV.", N, removed, N)
            if len(df_keep) > 0:
                df_keep.to_csv(RESULTS_CSV, index=False)
            else:
                os.remove(RESULTS_CSV)

        # Set up DB state: restore checkpoint from after N-1, or init fresh
        if N == 1:
            logger.info("[--request %d] Initializing DB from scratch.", N)
            init_database(db_engine)
            completed = {}
        elif _restore_checkpoint(N - 1):
            # DB is now in the exact state after request N-1 was processed
            completed = {
                k: v for k, v in _load_completed_request_ids(RESULTS_CSV).items()
                if k < N
            }
            logger.info(
                "[--request %d] Checkpoint restored — %d prior request(s) already done.",
                N, len(completed),
            )
        else:
            # No checkpoint exists yet — run all prerequisites from scratch
            logger.info(
                "[--request %d] No checkpoint for request %d — will run 1..%d first.",
                N, N - 1, N,
            )
            init_database(db_engine)
            completed = {}

        # Pending = any uncompleted requests in 1..N
        pending = sample[
            (sample["request_id"] <= N) &
            ~sample["request_id"].isin(completed.keys())
        ]

    elif force_rerun:
        logger.info("Initializing Database (--force-rerun: starting fresh)")
        init_database(db_engine)
        completed = {}
        pending = sample

    else:
        completed = _load_completed_request_ids(RESULTS_CSV)
        if completed:
            logger.info(
                "Resuming: %d completed, %d remaining.",
                len(completed), len(sample) - len(completed),
            )
        else:
            logger.info("Initializing Database...")
            init_database(db_engine)
        pending = sample[~sample["request_id"].isin(completed.keys())]

    if dry_run:
        logger.info("[DRY RUN] Processing first pending request only — no CSV written.")
        pending = pending.head(1)
    elif limit and target_request is None:
        pending = pending.head(limit)

    if pending.empty:
        logger.info("All requests already completed. Use --force-rerun to reprocess.")
        return [completed[i] for i in sorted(completed)]

    # ------------------------------------------------------------------
    # Initialise display state
    # ------------------------------------------------------------------
    initial_date = sample["request_date"].min().strftime("%Y-%m-%d")
    report = generate_financial_report(initial_date)
    current_cash = report["cash_balance"]
    current_inventory = report["inventory_value"]

    # Generate a unique run_id for token tracking (shared across all requests in this run)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    set_run_id(run_id)
    logger.info("Run ID: %s", run_id)

    results_map = dict(completed)

    # ------------------------------------------------------------------
    # Process each pending request
    # ------------------------------------------------------------------
    for _, row in pending.iterrows():
        request_id   = int(row["request_id"])
        request_date = row["request_date"].strftime("%Y-%m-%d")

        logger.info(
            "\n=== Request %d / %d === [%s | %s | %s] Cash: $%.2f",
            request_id, len(sample), request_date, row["job"], row["event"], current_cash,
        )
        logger.debug("Inventory value before request: $%.2f", current_inventory)

        # Tag all LLM calls in this request with request_id for token_usage.csv
        set_request_context(request_id)

        request_with_date = f"{row['request']} (Date of request: {request_date})"

        try:
            deps = OrchestratorDeps(run_id=run_id, request_id=request_id, request_date=request_date)
            response = run_with_retry(request_with_date, deps)
        except Exception as exc:
            # Non-retryable error (budget, auth) — record and continue
            response = f"[ERROR] Could not process request: {exc}"
            logger.error("Request %d failed (non-retryable): %s", request_id, exc)

        report = generate_financial_report(request_date)
        current_cash      = report["cash_balance"]
        current_inventory = report["inventory_value"]

        logger.info(
            "Request %d done — Cash after: $%.2f | Response: %s%s",
            request_id, current_cash,
            response[:120], "..." if len(response) > 120 else "",
        )
        logger.debug("Full response for request %d:\n%s", request_id, response)

        # --- Interactive pause: show response and wait for approval ---
        if interactive:
            print(f"\n{'='*60}")
            print(f"REQUEST {request_id} RESPONSE:")
            print(f"{'='*60}")
            print(response)
            print(f"{'='*60}")
            print(f"Cash before: ${report['cash_balance'] - (current_cash - report['cash_balance']):.2f}  →  after: ${current_cash:.2f}")
            print(f"Token usage logged to: {TOKEN_CSV.name}")
            user_input = input("\nPress Enter to continue to next request, or 'q' to stop: ").strip().lower()
            if user_input == "q":
                logger.info("Interactive mode: user stopped after request %d.", request_id)
                results_map[request_id] = {
                    "request_id": request_id, "request_date": request_date,
                    "cash_balance": current_cash, "inventory_value": current_inventory,
                    "response": response,
                }
                if not dry_run:
                    ordered = [results_map[i] for i in sorted(results_map)]
                    pd.DataFrame(ordered).to_csv(RESULTS_CSV, index=False)
                return [results_map[i] for i in sorted(results_map)]

        results_map[request_id] = {
            "request_id":      request_id,
            "request_date":    request_date,
            "cash_balance":    current_cash,
            "inventory_value": current_inventory,
            "response":        response,
        }

        # Persist after every request — safe checkpoint on crash or budget cut-off
        if not dry_run:
            ordered = [results_map[i] for i in sorted(results_map)]
            pd.DataFrame(ordered).to_csv(RESULTS_CSV, index=False)
            logger.info("[Saved] %s (%d/%d rows)", RESULTS_CSV, len(results_map), len(sample))
            _save_checkpoint(request_id)

    # ------------------------------------------------------------------
    # Final summary
    # ------------------------------------------------------------------
    final_date   = sample["request_date"].max().strftime("%Y-%m-%d")
    final_report = generate_financial_report(final_date)
    logger.info(
        "\n===== FINAL FINANCIAL REPORT =====\n"
        "  Cash:      $%.2f\n  Inventory: $%.2f\n  Assets:    $%.2f",
        final_report["cash_balance"],
        final_report["inventory_value"],
        final_report["total_assets"],
    )

    if dry_run:
        logger.info("[DRY RUN] Complete — no CSV written. Run without --dry-run when ready.")
    else:
        total_done = len(results_map)
        logger.info("%s — %d/%d requests recorded.", RESULTS_CSV, total_done, len(sample))
        if total_done < len(sample):
            logger.info("Re-run without flags to continue from where we left off.")

    return [results_map[i] for i in sorted(results_map)]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    # Usage:
    #   python project_starter.py                   → resume (skip completed, run remaining)
    #   python project_starter.py --dry-run         → 1 request, no CSV write (budget check)
    #   python project_starter.py --force-rerun     → ignore results, reinit DB, run all 20
    #   python project_starter.py --limit 5         → process at most 5 pending requests
    #   python project_starter.py --request 3       → run up to request 3; re-run 3 if it
    #                                                   already exists (restores DB checkpoint)
    dry_run        = "--dry-run"     in sys.argv
    force_rerun    = "--force-rerun" in sys.argv
    interactive    = "--interactive" in sys.argv
    limit          = None
    target_request = None

    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    if "--request" in sys.argv:
        target_request = int(sys.argv[sys.argv.index("--request") + 1])

    results = run_test_scenarios(
        dry_run=dry_run, limit=limit,
        force_rerun=force_rerun, interactive=interactive,
        target_request=target_request,
    )
