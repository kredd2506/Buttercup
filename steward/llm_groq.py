"""Groq LLM wrapper — the reasoning/generation engine.

Groq serves open models (Llama, etc.) on an OpenAI-compatible API. We use the
native `groq` SDK here; swapping to the `openai` SDK pointed at
https://api.groq.com/openai/v1 would also work and keeps you provider-agnostic.
"""

from __future__ import annotations

import json
from typing import Any

from groq import Groq

from .config import CONFIG

_client = Groq(api_key=CONFIG.groq_api_key)


def complete(system: str, user: str, temperature: float = 0.2) -> str:
    """Plain text completion."""
    resp = _client.chat.completions.create(
        model=CONFIG.groq_model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content or ""


def complete_json(system: str, user: str, temperature: float = 0.1) -> dict[str, Any]:
    """Structured completion. Asks the model for a JSON object and parses it.

    Uses response_format=json_object when supported; falls back to fence-stripping.
    """
    system_json = (
        system
        + "\n\nRespond with a single valid JSON object and nothing else. "
        "No markdown, no code fences, no commentary."
    )
    try:
        resp = _client.chat.completions.create(
            model=CONFIG.groq_model,
            temperature=temperature,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_json},
                {"role": "user", "content": user},
            ],
        )
        text = resp.choices[0].message.content or "{}"
    except Exception:
        # Model/endpoint may not support response_format — retry plain.
        text = complete(system_json, user, temperature)

    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(text)
