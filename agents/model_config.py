"""
agents/model_config.py — Shared LLM model instance and retry configuration.

All agent modules import `llm_model` from here so the API key and endpoint
are configured in exactly one place.
"""

import os
import time
import dotenv
from smolagents import OpenAIServerModel

# Load .env from the project root (one level up from this file)
dotenv.load_dotenv()

# ---------------------------------------------------------------------------
# LLM model — gpt-4o-mini is ~15x cheaper than gpt-4o and sufficient for
# tool-calling agents in this domain.
# ---------------------------------------------------------------------------
llm_model = OpenAIServerModel(
    model_id="gpt-4o-mini",
    api_base="https://openai.vocareum.com/v1",
    api_key=os.environ.get("UDACITY_OPENAI_API_KEY", ""),
)

# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------
MAX_RETRIES = 5

# Only these error signatures warrant a retry — budget/auth errors do not.
RETRYABLE_KEYWORDS = (
    "rate limit", "429", "timeout", "connection", "503", "502", "500", "overloaded"
)


def run_with_retry(agent, task: str, max_retries: int = MAX_RETRIES) -> str:
    """
    Execute agent.run(task) with exponential-backoff retry on transient errors.

    Retries up to max_retries times when the error message contains a known
    transient keyword (rate limit, server error, timeout). Permanent errors
    such as budget exhaustion or authentication failures are re-raised
    immediately without retrying.

    Args:
        agent:       A smolagents agent with a .run() method.
        task:        The task string to pass to agent.run().
        max_retries: Maximum number of attempts (default: MAX_RETRIES).

    Returns:
        The agent's response string on success.

    Raises:
        The last exception if all retries are exhausted or error is permanent.
    """
    for attempt in range(max_retries):
        try:
            return agent.run(task)
        except Exception as exc:
            is_retryable = any(kw in str(exc).lower() for kw in RETRYABLE_KEYWORDS)
            if not is_retryable or attempt == max_retries - 1:
                raise
            # Exponential backoff: 2 s, 4 s, 8 s, 16 s, …
            wait = 2 ** (attempt + 1)
            print(f"[Retry {attempt + 1}/{max_retries}] Waiting {wait}s — {exc}")
            time.sleep(wait)
