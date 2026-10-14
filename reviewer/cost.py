"""Token counting and USD cost tracking for LLM review calls.

Token counts use tiktoken with the configured model's encoding when tiktoken
is installed; otherwise they fall back to a documented len(text)//4
approximation (the same rough heuristic the chunker uses for budgeting, so
costs stay in the same ballpark either way).

Prices are USD per 1M tokens. The built-in table covers common OpenAI models;
any model can be added or repriced through the config file's ``pricing:``
section. Models with no price data report $0 with a warning.
"""

import logging

log = logging.getLogger(__name__)

#: Fallback heuristic when tiktoken is unavailable: chars per token.
CHARS_PER_TOKEN = 4

#: Default prices as (prompt, completion) USD per 1M tokens.
DEFAULT_PRICES = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "o4-mini": (1.10, 4.40),
    "o3-mini": (1.10, 4.40),
}


def count_tokens(text, model):
    """Count tokens in ``text`` for ``model``.

    Uses tiktoken when installed, else the documented len(text)//4 fallback.
    """
    try:
        import tiktoken
    except ImportError:
        return max(1, len(text or "") // CHARS_PER_TOKEN)
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        log.debug("tiktoken has no encoding for model %r; using cl100k_base.", model)
        encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(text or ""))


def resolve_prices(model, overrides=None):
    """Return (prompt, completion) USD-per-1M-tokens for ``model``.

    Config-file ``pricing:`` overrides win over the built-in table.
    """
    if overrides and model in overrides:
        entry = overrides[model]
        return float(entry["prompt"]), float(entry["completion"])
    if model in DEFAULT_PRICES:
        return DEFAULT_PRICES[model]
    log.warning("No price data for model %r; cost will be reported as $0.", model)
    return (0.0, 0.0)


class CostTracker:
    """Accumulates prompt/completion tokens and estimated USD cost per run."""

    def __init__(self, model, pricing_overrides=None):
        self.model = model
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.prompt_price, self.completion_price = resolve_prices(
            model, pricing_overrides
        )

    def record(self, prompt_tokens, completion_tokens):
        """Add one LLM call's token usage."""
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens

    @property
    def total_tokens(self):
        return self.prompt_tokens + self.completion_tokens

    @property
    def cost_usd(self):
        return (
            self.prompt_tokens / 1_000_000 * self.prompt_price
            + self.completion_tokens / 1_000_000 * self.completion_price
        )

    def summary_line(self):
        """One-line human-readable cost summary for the run output."""
        return (
            f"LLM cost: ~${self.cost_usd:.4f} "
            f"({self.prompt_tokens:,} prompt + {self.completion_tokens:,} completion "
            f"tokens, {self.model})"
        )

    def as_dict(self):
        return {
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }
