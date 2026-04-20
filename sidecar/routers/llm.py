"""LLM proxy router — forwards chat requests to the configured relay."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter()


def _settings_path() -> Path:
    env_path = os.environ.get("SCIPILOT_SETTINGS_PATH", "").strip()
    if env_path:
        return Path(env_path)
    return Path(__file__).resolve().parents[3] / "scipilot" / "settings.json"


def _load_settings() -> dict[str, Any]:
    try:
        return json.loads(_settings_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _provider_key(provider: str) -> str:
    normalized = str(provider or "llm").strip().lower()
    if normalized in {"llm", "openai", "gpt", "claude", "anthropic"}:
        return "llm"
    if normalized == "ollama":
        return "ollama"
    return normalized


def _resolve_config(provider: str, settings: dict[str, Any]) -> tuple[str, str, str]:
    provider_key = _provider_key(provider)
    api_keys = settings.get("api_keys", {}) or {}
    base_urls = settings.get("api_base_urls", {}) or {}

    if provider_key == "ollama":
        return (
            provider_key,
            "",
            str(base_urls.get("ollama") or "http://localhost:11434/api/chat"),
        )

    raw_base_url = str(base_urls.get(provider_key) or "https://api.openai.com/v1/chat/completions")
    normalized_base_url = (
        _normalize_anthropic_url(raw_base_url)
        if _uses_anthropic_api(raw_base_url)
        else _normalize_openai_url(raw_base_url)
    )

    return (
        provider_key,
        str(api_keys.get(provider_key) or ""),
        normalized_base_url,
    )



def _uses_anthropic_api(base_url: str) -> bool:
    normalized = base_url.strip().lower()
    return "/v1/messages" in normalized or normalized.endswith("/messages") or "anthropic" in normalized



def _normalize_openai_url(base_url: str) -> str:
    raw = (base_url or "").strip()
    if not raw:
        return "https://api.openai.com/v1/chat/completions"
    trimmed = raw.rstrip("/")
    normalized = trimmed.lower()
    if normalized.endswith("/chat/completions"):
        return trimmed
    if normalized.endswith("/v1"):
        return f"{trimmed}/chat/completions"
    return f"{trimmed}/v1/chat/completions"



def _normalize_anthropic_url(base_url: str) -> str:
    raw = (base_url or "").strip()
    if not raw:
        return "https://api.anthropic.com/v1/messages"
    trimmed = raw.rstrip("/")
    normalized = trimmed.lower()
    if normalized.endswith("/v1/messages") or normalized.endswith("/messages"):
        return trimmed
    if normalized.endswith("/v1"):
        return f"{trimmed}/messages"
    return f"{trimmed}/v1/messages"


def _build_headers(provider: str, api_key: str) -> dict[str, str]:
    headers = {
        "content-type": "application/json",
        "accept": "text/event-stream",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    if provider == "ollama":
        return headers
    if provider == "llm" and api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _normalize_payload_for_upstream(provider: str, base_url: str, body: dict[str, Any]) -> dict[str, Any]:
    payload = dict(body)
    payload.pop("provider", None)
    if provider == "llm" and _uses_anthropic_api(base_url):
        messages = list(payload.get("messages") or [])
        system_message = None
        filtered_messages = []
        for message in messages:
            if message.get("role") == "system" and system_message is None:
                system_message = str(message.get("content") or "")
                continue
            filtered_messages.append(message)
        payload["messages"] = filtered_messages
        if system_message:
            payload["system"] = system_message
        payload.setdefault("max_tokens", 4096)
    return payload


@router.post("/llm/stream")
async def llm_stream(request: Request):
    body = await request.json()
    settings = _load_settings()
    provider, api_key, base_url = _resolve_config(str(body.get("provider") or "llm"), settings)

    if provider != "ollama" and not api_key:
        raise HTTPException(status_code=400, detail=f"{provider} API key not configured")

    payload = _normalize_payload_for_upstream(provider, base_url, body)
    headers = _build_headers(provider, api_key)
    if provider == "llm" and _uses_anthropic_api(base_url):
        headers.pop("Authorization", None)
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"

    async def generate():
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            async with client.stream("POST", base_url, headers=headers, json=payload) as resp:
                if resp.status_code >= 400:
                    detail = await resp.aread()
                    raise HTTPException(status_code=resp.status_code, detail=detail.decode("utf-8", errors="ignore"))

                if provider == "ollama":
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                        except Exception:
                            continue
                        delta = ((chunk.get("message") or {}).get("content") or "")
                        if delta:
                            yield f"data: {json.dumps({'choices': [{'delta': {'content': delta}}]})}\n\n".encode("utf-8")
                        if chunk.get("done") is True:
                            yield b"data: [DONE]\n\n"
                            return
                    yield b"data: [DONE]\n\n"
                    return

                async for chunk in resp.aiter_bytes():
                    yield chunk

    return StreamingResponse(generate(), media_type="text/event-stream")
