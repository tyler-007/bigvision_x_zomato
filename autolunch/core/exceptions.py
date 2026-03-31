from __future__ import annotations
"""
AutoLunch — Core Exception Hierarchy

All exceptions inherit from AutoLunchError for clean catch-all handling.
Each domain (Zomato, LLM, Budget, Auth) has its own exception class
so n8n's error handler can route them differently if needed.
"""


class AutoLunchError(Exception):
    """Base exception for all AutoLunch errors."""

    def __init__(self, message: str, context: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context = context or {}

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(message={self.message!r}, context={self.context!r})"


# ── Configuration ─────────────────────────────────────────────────────────────


class ConfigurationError(AutoLunchError):
    """Raised when required configuration (env vars, JSON files) is missing or malformed."""


# ── Zomato / MCP ─────────────────────────────────────────────────────────────


class ZomatoError(AutoLunchError):
    """Base for all Zomato MCP errors."""


class ZomatoAuthError(ZomatoError):
    """OAuth token expired or invalid — triggers Telegram retry-auth alert."""


class ZomatoServerError(ZomatoError):
    """Zomato MCP server is down or returned 5xx — triggers manual-order Telegram alert."""


class ZomatoNoResultsError(ZomatoError):
    """No restaurants found matching criteria (location, rating, distance)."""


# ── Budget ────────────────────────────────────────────────────────────────────


class BudgetExceededError(AutoLunchError):
    """Raised when cart simulation shows net total > configured max budget."""

    def __init__(self, net_total: float, budget: float) -> None:
        super().__init__(
            f"Net total ₹{net_total} exceeds budget ₹{budget}",
            context={"net_total": net_total, "budget": budget},
        )
        self.net_total = net_total
        self.budget = budget


class MaxRetriesExceededError(AutoLunchError):
    """LLM failed to find a meal within budget after configured max attempts."""


# ── LLM / OpenRouter ─────────────────────────────────────────────────────────


class LLMError(AutoLunchError):
    """Base for OpenRouter / LLM errors."""


class LLMRateLimitError(LLMError):
    """OpenRouter rate limit hit — will retry with backoff via tenacity."""


class LLMResponseParseError(LLMError):
    """LLM returned malformed JSON that couldn't be parsed into expected schema."""


# ── HITL / User Interaction ───────────────────────────────────────────────────


class HITLRejectionLimitError(AutoLunchError):
    """User rejected all suggestions — system falls back to manual order."""


# ── Sheets / Drive ───────────────────────────────────────────────────────────


class SheetsError(AutoLunchError):
    """Google Sheets or Drive API error during expense logging."""
