"""Single place that decides which chat model the app uses.

Provider is picked by the LLM_PROVIDER env var ("groq" | "anthropic").
If unset, Anthropic is used when ANTHROPIC_API_KEY exists, otherwise Groq
(free tier, https://console.groq.com) — so the hosted demo runs at $0.
On Streamlit Community Cloud, top-level secrets are exposed as env vars.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel

load_dotenv(override=True)

ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
GROQ_MODEL = "llama-3.3-70b-versatile"


def get_provider() -> str:
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    if provider:
        return provider
    return "anthropic" if os.getenv("ANTHROPIC_API_KEY") else "groq"


def get_llm(max_tokens: int = 2500, temperature: float = 0) -> BaseChatModel:
    provider = get_provider()
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=os.getenv("ANTHROPIC_MODEL", ANTHROPIC_MODEL),
            temperature=temperature,
            max_tokens=max_tokens,
        )
    if provider == "groq":
        from langchain_groq import ChatGroq

        return ChatGroq(
            model=os.getenv("GROQ_MODEL", GROQ_MODEL),
            temperature=temperature,
            max_tokens=max_tokens,
        )
    raise ValueError(f"Unsupported LLM_PROVIDER '{provider}'. Use 'groq' or 'anthropic'.")
