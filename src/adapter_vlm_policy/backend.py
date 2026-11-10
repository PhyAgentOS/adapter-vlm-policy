from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .config import ModelRoute
from .errors import ServiceError


@dataclass
class Completion:
    text: str
    model_id: str
    usage: dict[str, int | None]


class Backend(Protocol):
    async def complete(
        self,
        route: ModelRoute,
        messages: list[dict],
        max_tokens: int,
        schema: dict | None = None,
    ) -> Completion: ...


class CompatibleBackend:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(180), follow_redirects=False)

    async def close(self):
        await self.client.aclose()

    async def complete(self, route, messages, max_tokens, schema=None):
        base = route.base_url.rstrip("/")
        if not base or not route.model:
            raise ServiceError("MODEL_UNAVAILABLE", "Model route is not configured", 503)
        url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        payload: dict[str, Any] = {
            "model": route.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        if route.reasoning_effort is not None:
            payload["reasoning_effort"] = route.reasoning_effort
        if schema is not None and route.native_json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "strict": True, "schema": schema},
            }
        headers = {"Authorization": f"Bearer {route.api_key}"} if route.api_key else {}
        try:
            response = await self.client.post(url, json=payload, headers=headers)
        except httpx.RequestError as exc:
            raise ServiceError("MODEL_UNAVAILABLE", "Model transport failed", 502) from exc
        if response.status_code != 200:
            # Provider response bodies may echo credentials or input: do not expose them.
            raise ServiceError(
                "MODEL_UNAVAILABLE",
                f"Model backend returned HTTP {response.status_code}",
                502,
            )
        try:
            data = response.json()
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ServiceError("INVALID_OUTPUT", "Model output was truncated", 502)
            content = choice["message"]["content"]
            if isinstance(content, list):
                content = "".join(p.get("text", "") for p in content if p.get("type") == "text")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty content")
            usage = data.get("usage") or {}
            return Completion(
                content,
                data.get("model", route.model),
                {
                    "input_tokens": usage.get("prompt_tokens"),
                    "output_tokens": usage.get("completion_tokens"),
                },
            )
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise ServiceError("INVALID_OUTPUT", "Malformed model response", 502) from exc
