"""NVIDIA Nemotron via Nebius Token Factory -- the project's AI layer.

Configuration (names only; values live in your .env, see .env.example):
    NEBIUS_API_KEY     secret -- NEVER logged, printed, echoed, or returned
    NEBIUS_BASE_URL    OpenAI-compatible endpoint (official default baked in)
    NEBIUS_MODEL       exact model id from your account's model catalog
    NEBIUS_TIMEOUT     request timeout in seconds (default 30)

Verified against the official Nebius docs (Oct 2026):
    - Base URL  https://api.tokenfactory.nebius.com/v1/
    - Client    the official `openai` Python SDK (OpenAI-compatible API)
    - Models    official catalog lists e.g. nvidia/nemotron-3-super-120b-a12b

Security rules enforced in this module:
- Missing configuration raises AIConfigurationError naming the missing
  VARIABLE NAMES only; credential values never enter any message or log.
- Every SDK/network failure maps to a typed AIError whose str() is safe
  to show API clients; raw exceptions stay server-side, and logs record
  only the exception type -- never request/response bodies.
- No silent fallbacks: requests either go to the configured NEBIUS_MODEL
  or fail loudly with a configuration error. No other provider, ever.

This module is deliberately transport-only: it knows nothing about
Django models or endpoints. Whoever calls it (views, services) decides
what to ask and what to do with the answer.
"""

import json
import logging

from django.conf import settings
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

logger = logging.getLogger(__name__)

# Hard bound on a single completion. Expected replies are small JSON objects
# (one minimal patch, or one explanation), so 4096 tokens is generous while
# still capping worst-case latency and transfer for a runaway generation.
MAX_COMPLETION_TOKENS = 4096


class AIError(Exception):
    """Base AI failure. str(exc) is always safe to show a client."""


class AIConfigurationError(AIError):
    """Required environment variables are missing or empty."""


class AITimeout(AIError):
    """The AI endpoint did not answer within the configured timeout."""


class AIUnavailable(AIError):
    """The AI endpoint errored, refused the call, or returned garbage."""


def missing_configuration() -> list:
    """Names of unset variables. Returns names ONLY, never values."""
    missing = []
    if not getattr(settings, "NEBIUS_API_KEY", ""):
        missing.append("NEBIUS_API_KEY")
    if not getattr(settings, "NEBIUS_MODEL", ""):
        missing.append("NEBIUS_MODEL")
    if not getattr(settings, "NEBIUS_BASE_URL", ""):
        missing.append("NEBIUS_BASE_URL")
    return missing


def is_configured() -> bool:
    """True when all required variables are present (for feature gates)."""
    return not missing_configuration()


def get_client() -> OpenAI:
    """Build the official OpenAI SDK client pointed at Nebius Token Factory.

    Raises AIConfigurationError naming the missing variables (names only).
    """
    missing = missing_configuration()
    if missing:
        raise AIConfigurationError(
            "AI is not configured. Add these to your .env "
            f"(see .env.example): {', '.join(missing)}."
        )
    return OpenAI(
        api_key=settings.NEBIUS_API_KEY,
        base_url=settings.NEBIUS_BASE_URL,
        timeout=settings.NEBIUS_TIMEOUT_SECONDS,
    )


def chat_json(system_prompt: str, user_prompt: str,
              *, temperature: float = 0.2,
              max_tokens: int = MAX_COMPLETION_TOKENS) -> dict:
    """One JSON-mode chat completion against the configured Nemotron model.

    Returns the parsed JSON object from the model's reply.

    `max_tokens` bounds the completion size: expected replies are small JSON
    objects (one patch or one explanation), so a runaway generation is cut
    off instead of billing and transferring an unbounded response. A reply
    truncated mid-JSON fails JSON parsing and surfaces as AIUnavailable --
    never as a silently half-applied patch.

    Raises:
        AIConfigurationError -- required variables missing (names only)
        AITimeout            -- endpoint exceeded NEBIUS_TIMEOUT
        AIUnavailable        -- auth/rate/network/format problems

    Note: because response_format is json_object, the system prompt must
    mention JSON (provider requirement for JSON mode).
    """
    client = get_client()   # validates configuration first

    try:
        response = client.chat.completions.create(
            model=settings.NEBIUS_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
    except APITimeoutError:
        # Must precede APIConnectionError (it is a subclass).
        raise AITimeout(
            f"AI did not respond within "
            f"{settings.NEBIUS_TIMEOUT_SECONDS} seconds."
        )
    except AuthenticationError:
        # Fixed, hand-written text: nothing from the server or SDK can
        # echo credential material back out through this message.
        raise AIUnavailable(
            "Nebius authentication failed. Check NEBIUS_API_KEY."
        )
    except RateLimitError:
        raise AIUnavailable(
            "AI rate limit or budget exhausted. Check the "
            "Token Factory project budget/quota."
        )
    except APIConnectionError:
        raise AIUnavailable("AI service is unreachable.")
    except AIError:
        raise
    except Exception as exc:
        # Unexpected SDK failure: log ONLY the type -- never the message
        # body, which could theoretically contain request details.
        logger.error("Nebius call failed: %s", type(exc).__name__)
        raise AIUnavailable(f"AI request failed ({type(exc).__name__}).")

    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError):
        raise AIUnavailable("AI returned an unexpected response shape.")
    if not content:
        raise AIUnavailable("AI returned an empty response.")

    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        raise AIUnavailable("AI did not return valid JSON.")
    if not isinstance(payload, dict):
        raise AIUnavailable("AI returned JSON that is not an object.")
    return payload
