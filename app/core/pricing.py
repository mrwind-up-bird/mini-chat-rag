"""Centralized model pricing configuration.

Single source of truth for LLM token costs (USD per 1M tokens).
Used by stats endpoints and exposed via GET /v1/stats/pricing for the dashboard.
"""

# Maps model identifiers to (prompt_cost, completion_cost) per 1M tokens.
# Updated periodically — unknown models fall back to a conservative estimate.
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o":              (2.50,  10.00),
    "gpt-4o-mini":         (0.15,   0.60),
    "gpt-4-turbo":         (10.00,  30.00),
    "gpt-4":               (30.00,  60.00),
    "gpt-3.5-turbo":       (0.50,   1.50),
    "o1":                  (15.00,  60.00),
    "o1-mini":             (3.00,   12.00),
    "o3-mini":             (1.10,   4.40),
    # Anthropic (via LiteLLM)
    "claude-opus-4-6":                (15.00, 75.00),
    "claude-sonnet-4-5-20250929":     (3.00,  15.00),
    "claude-haiku-4-5-20251001":      (0.80,   4.00),
    # Google (via LiteLLM)
    "gemini/gemini-2.0-flash":        (0.10,   0.40),
    "gemini/gemini-1.5-pro":          (1.25,   5.00),
    "gemini/gemini-1.5-flash":        (0.075,  0.30),
}

DEFAULT_PRICING: tuple[float, float] = (1.00, 3.00)


def get_pricing(model: str) -> tuple[float, float]:
    """Return (prompt_per_1M, completion_per_1M) for a model."""
    return MODEL_PRICING.get(model, DEFAULT_PRICING)


def calc_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate USD cost for a given model and token counts."""
    prompt_rate, completion_rate = get_pricing(model)
    return (prompt_tokens * prompt_rate + completion_tokens * completion_rate) / 1_000_000
