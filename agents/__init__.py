"""
agents/__init__.py — Public interface for the Munder Difflin agent package.

Import the orchestrator, structured types, and run_with_retry from here;
the worker agents (inventory, quoting, sales) are managed internally by the orchestrator.
"""

from .orchestrator_agent import orchestrator_agent, OrderResponse, OrchestratorDeps, run_with_retry
from .model_config import set_request_context, set_run_id, TOKEN_CSV, log_usage

__all__ = [
    "orchestrator_agent",
    "OrderResponse",
    "OrchestratorDeps",
    "run_with_retry",
    "set_request_context",
    "set_run_id",
    "TOKEN_CSV",
    "log_usage",
]
