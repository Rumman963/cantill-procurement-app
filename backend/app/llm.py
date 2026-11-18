from __future__ import annotations

import json
import os
import re

import httpx


def provider() -> str | None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return None


def available() -> bool:
    return provider() is not None


def complete(system: str, user: str, max_tokens: int = 4000, timeout: float = 90) -> str:
    p = provider()
    if p == "anthropic":
        r = httpx.post("https://api.anthropic.com/v1/messages", timeout=timeout, headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
            "content-type": "application/json"}, json={
            "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5"), "max_tokens": max_tokens,
            "temperature": 0, "system": system, "messages": [{"role": "user", "content": user}]})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json()["content"])
    if p == "openai":
        r = httpx.post("https://api.openai.com/v1/chat/completions", timeout=timeout, headers={
            "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}, json={
            "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"), "temperature": 0,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    if p == "gemini":
        model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                       params={"key": os.environ["GEMINI_API_KEY"]}, timeout=timeout, json={
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": max_tokens}})
        r.raise_for_status()
        return "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
    raise RuntimeError("No LLM provider configured")


def extract_json(text: str):
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("No JSON in model output")
    return json.loads(text[start:text.rfind("}" if text[start] == "{" else "]") + 1])
