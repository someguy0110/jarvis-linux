import os
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class OpenRouterConfig:
    api_key: str
    base_url: str = "https://openrouter.ai/api/v1"
    fast_model: str = "anthropic/claude-3.5-haiku"
    research_model: str = "anthropic/claude-3.5-sonnet"
    vision_model: str = "anthropic/claude-3.5-sonnet"
    site_url: str = ""
    app_name: str = "JARVIS"


class OpenRouterClient:
    def __init__(self, config: OpenRouterConfig):
        self._cfg = config

    @property
    def configured(self) -> bool:
        return bool(self._cfg.api_key.strip())

    @property
    def fast_model(self) -> str:
        return self._cfg.fast_model

    @property
    def research_model(self) -> str:
        return self._cfg.research_model

    @property
    def vision_model(self) -> str:
        return self._cfg.vision_model

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        temperature: float | None = None,
    ) -> str:
        data = await self.chat_raw(messages=messages, model=model, max_tokens=max_tokens, temperature=temperature)
        return (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""

    async def chat_raw(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self._cfg.api_key}",
            "Content-Type": "application/json",
        }
        if self._cfg.site_url:
            headers["HTTP-Referer"] = self._cfg.site_url
        if self._cfg.app_name:
            headers["X-Title"] = self._cfg.app_name

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            payload["temperature"] = temperature

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{self._cfg.base_url}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()


def load_openrouter_client() -> OpenRouterClient:
    cfg = OpenRouterConfig(
        api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip(),
        fast_model=os.getenv("OPENROUTER_FAST_MODEL", "anthropic/claude-3.5-haiku").strip(),
        research_model=os.getenv("OPENROUTER_RESEARCH_MODEL", "anthropic/claude-3.5-sonnet").strip(),
        vision_model=os.getenv("OPENROUTER_VISION_MODEL", "anthropic/claude-3.5-sonnet").strip(),
        site_url=os.getenv("OPENROUTER_SITE_URL", "").strip(),
        app_name=os.getenv("OPENROUTER_APP_NAME", "JARVIS").strip(),
    )
    return OpenRouterClient(cfg)
