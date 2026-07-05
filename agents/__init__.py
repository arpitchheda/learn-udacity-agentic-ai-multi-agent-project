"""
agents/__init__.py — Public interface for the Munder Difflin agent package.

Import the orchestrator and run_with_retry from here; the worker agents
(inventory, quoting, sales) are managed internally by the orchestrator.
"""

from agents.orchestrator_agent import orchestrator_agent
from agents.inventory_agent import inventory_agent
from agents.quoting_agent import quoting_agent
from agents.sales_agent import sales_agent
from agents.model_config import run_with_retry

__all__ = [
    "orchestrator_agent",
    "inventory_agent",
    "quoting_agent",
    "sales_agent",
    "run_with_retry",
]
