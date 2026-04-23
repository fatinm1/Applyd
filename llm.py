"""
llm.py — Minimal LLM adapter.

Design goals:
- Keep dependencies at zero (uses `requests`, already in requirements.txt).
- Default to "none" so the project is free/useful for everyone.
- Support free local AI via Ollama for power users running locally 24/7.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import requests

from config import config

log = logging.getLogger(__name__)


def llm_enabled() -> bool:
    provider = (config.LLM_PROVIDER or "none").lower()
    if provider == "none":
        return False
    if provider == "anthropic":
        return bool(config.ANTHROPIC_API_KEY)
    if provider == "ollama":
        return True
    return False


def _strip_code_fences(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    return t


def complete_text(*, prompt: str, max_tokens: int = 800) -> str:
    """
    Return a plain text completion for `prompt`.
    Raises on unknown provider or request failures.
    """
    provider = (config.LLM_PROVIDER or "none").lower()

    if provider == "anthropic":
        if not config.ANTHROPIC_API_KEY:
            raise RuntimeError("LLM provider is anthropic but ANTHROPIC_API_KEY is missing.")
        import anthropic  # type: ignore

        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return (message.content[0].text or "").strip()

    if provider == "ollama":
        url = f"{config.OLLAMA_BASE_URL}/api/chat"
        payload = {
            "model": config.LLM_MODEL,
            "stream": False,
            "messages": [{"role": "user", "content": prompt}],
            # Keep the model on task for structured outputs.
            "options": {"temperature": 0.2},
        }
        r = requests.post(url, json=payload, timeout=config.OLLAMA_TIMEOUT_SECONDS)
        r.raise_for_status()
        data = r.json()
        content = (data.get("message") or {}).get("content") or ""
        return str(content).strip()

    if provider == "none":
        raise RuntimeError("LLM provider is 'none' (LLM disabled).")

    raise RuntimeError(f"Unknown LLM_PROVIDER: {provider!r}")


def complete_json(*, prompt: str, max_tokens: int = 800) -> dict[str, Any]:
    """
    Call `complete_text` and parse the response as JSON.
    We aggressively strip code fences to tolerate model quirks.
    """
    raw = complete_text(prompt=prompt, max_tokens=max_tokens)
    raw = _strip_code_fences(raw)
    return json.loads(raw)

