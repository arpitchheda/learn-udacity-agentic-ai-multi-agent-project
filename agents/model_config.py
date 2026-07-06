"""
agents/model_config.py — Model instances, token tracking, run/request context.

Public API:
    orchestrator_model  — gpt-4o for orchestrator
    specialist_model    — gpt-4o-mini for inventory/quoting/sales agents
    log_usage(agent_name, usage, request_id, run_id)
    set_request_context(request_id) / get_request_id()
    set_run_id(run_id) / get_run_id()
    TOKEN_CSV
    is_retryable(exc)
"""
import csv, os, threading
from datetime import datetime
from pathlib import Path
import dotenv
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from logger_config import get_logger

logger = get_logger("agents.model_config")
dotenv.load_dotenv()

_API_BASE = "https://openai.vocareum.com/v1"
_API_KEY  = os.environ.get("UDACITY_OPENAI_API_KEY", "")

# pydantic-ai 2.x uses OpenAIChatModel + OpenAIProvider
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

_provider = OpenAIProvider(base_url=_API_BASE, api_key=_API_KEY)

orchestrator_model = OpenAIChatModel("gpt-4o",       provider=_provider)
specialist_model   = OpenAIChatModel("gpt-4o-mini",  provider=_provider)

TOKEN_CSV          = Path(__file__).parent.parent / "token_usage.csv"
_TOKEN_CSV_HEADERS = ["run_id","request_id","agent","input_tokens","output_tokens","total_tokens","timestamp"]
_csv_lock = threading.Lock()


def _init_token_csv() -> None:
    """Create token_usage.csv with correct headers, resetting if headers are stale."""
    needs_reset = False
    if TOKEN_CSV.exists():
        try:
            with open(TOKEN_CSV, "r", encoding="utf-8") as fh:
                first_line = fh.readline().strip()
            if first_line != ",".join(_TOKEN_CSV_HEADERS):
                needs_reset = True
        except Exception:
            needs_reset = True
    if not TOKEN_CSV.exists() or needs_reset:
        with open(TOKEN_CSV, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(_TOKEN_CSV_HEADERS)


_init_token_csv()


def log_usage(agent_name: str, usage, request_id: int, run_id: str) -> None:
    """
    Append one usage row. usage is a pydantic-ai RunUsage object (property on AgentRunResult).

    pydantic-ai 2.x RunUsage fields: input_tokens, output_tokens, total_tokens.
    """
    if usage is None:
        return
    # pydantic-ai 2.x RunUsage uses input_tokens / output_tokens / total_tokens
    in_tok  = getattr(usage, "input_tokens",  None)
    out_tok = getattr(usage, "output_tokens", None)
    tot_tok = getattr(usage, "total_tokens",  None)
    if tot_tok is None and in_tok is not None and out_tok is not None:
        tot_tok = in_tok + out_tok
    row = {
        "run_id": run_id, "request_id": request_id, "agent": agent_name,
        "input_tokens": in_tok, "output_tokens": out_tok, "total_tokens": tot_tok,
        "timestamp": datetime.now().strftime("%Y%m%d %H%M%S"),
    }
    with _csv_lock:
        with open(TOKEN_CSV, "a", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=_TOKEN_CSV_HEADERS).writerow(row)
    logger.debug("[token] run=%s req=%s agent=%s in=%s out=%s total=%s",
                 run_id, request_id, agent_name, in_tok, out_tok, tot_tok)


_ctx     = threading.local()
_run_ctx = threading.local()


def set_request_context(request_id: int) -> None:
    """Call once per request to tag token rows with the request_id."""
    _ctx.request_id = request_id


def get_request_id() -> int:
    """Return the current request_id set by set_request_context."""
    return getattr(_ctx, "request_id", 0)


def set_run_id(run_id: str) -> None:
    """Call once per run to tag token rows with the run_id."""
    _run_ctx.run_id = run_id


def get_run_id() -> str:
    """Return the current run_id set by set_run_id."""
    return getattr(_run_ctx, "run_id", "unknown")


RETRYABLE_KEYWORDS = ("rate limit", "429", "timeout", "connection", "503", "502", "500", "overloaded")


def is_retryable(exc: Exception) -> bool:
    """Return True when an exception is a transient error worth retrying."""
    return any(kw in str(exc).lower() for kw in RETRYABLE_KEYWORDS)
