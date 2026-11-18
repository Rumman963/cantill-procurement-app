from __future__ import annotations

import json
import os
import re

import httpx

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_gemini_models: list[str] | None = None
last_model: str | None = None


class LLMError(RuntimeError):
    pass


def redact(text) -> str:
    s = str(text)
    s = re.sub(r"([?&]key=)[^&'\"\s]+", r"\1***", s)
    s = re.sub(r"AIza[0-9A-Za-z_\-]{20,}", "***", s)
    s = re.sub(r"AQ\.[0-9A-Za-z_\-.]{20,}", "***", s)
    s = re.sub(r"sk-[0-9A-Za-z_\-]{16,}", "***", s)
    s = re.sub(r"https?://\S*googleapis\.com\S*", "[gemini endpoint]", s)
    return s


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


def _gemini_candidates(key: str, timeout: float) -> list[str]:
    global _gemini_models
    if _gemini_models is not None:
        return _gemini_models
    names = []
    try:
        r = httpx.get(f"{GEMINI_BASE}/models", headers={"x-goog-api-key": key}, params={"pageSize": 200}, timeout=timeout)
        r.raise_for_status()
        for m in r.json().get("models", []):
            name = m.get("name", "").replace("models/", "")
            if "generateContent" not in m.get("supportedGenerationMethods", []):
                continue
            if any(x in name for x in ("image", "tts", "audio", "live", "embedding", "vision", "learnlm", "gemma")):
                continue
            names.append(name)
    except httpx.HTTPError:
        pass

    def rank(n: str):
        m = re.search(r"(\d+(?:\.\d+)?)", n)
        stable = not any(x in n for x in ("preview", "exp"))
        return ("flash" in n, "lite" not in n, stable, float(m.group(1)) if m else 0.0)

    names.sort(key=rank, reverse=True)
    _gemini_models = names[:4]
    return _gemini_models


def _gemini(system: str, user: str, max_tokens: int, timeout: float) -> str:
    key = os.environ["GEMINI_API_KEY"]
    wanted = os.environ.get("GEMINI_MODEL")
    models = ([wanted] if wanted else []) + [m for m in _gemini_candidates(key, 20) if m != wanted]
    if not models:
        models = ["gemini-2.5-flash", "gemini-flash-latest"]
    global last_model
    last = None
    for model in models[:3]:
        try:
            r = httpx.post(f"{GEMINI_BASE}/models/{model}:generateContent", headers={"x-goog-api-key": key},
                           timeout=timeout, json={
                               "systemInstruction": {"parts": [{"text": system}]},
                               "contents": [{"role": "user", "parts": [{"text": user}]}],
                               "generationConfig": {"temperature": 0, "maxOutputTokens": max_tokens}})
        except httpx.HTTPError as e:
            last = f"{model}: network error {type(e).__name__}"
            continue
        if r.status_code in (404, 400) and "not found" in r.text.lower():
            last = f"{model}: model not available"
            continue
        if r.status_code >= 400:
            raise LLMError(f"Gemini {model} returned HTTP {r.status_code}")
        parts = (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        last_model = model
        return "".join(p.get("text", "") for p in parts if not p.get("thought"))
    raise LLMError(f"No Gemini model worked ({last})")


def complete(system: str, user: str, max_tokens: int = 4000, timeout: float = 90) -> str:
    p = provider()
    try:
        if p == "anthropic":
            r = httpx.post("https://api.anthropic.com/v1/messages", timeout=timeout, headers={
                "x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                "content-type": "application/json"}, json={
                "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5"), "max_tokens": max_tokens,
                "temperature": 0, "system": system, "messages": [{"role": "user", "content": user}]})
            if r.status_code >= 400:
                raise LLMError(f"Anthropic returned HTTP {r.status_code}")
            return "".join(b.get("text", "") for b in r.json()["content"])
        if p == "openai":
            r = httpx.post("https://api.openai.com/v1/chat/completions", timeout=timeout, headers={
                "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}, json={
                "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"), "temperature": 0,
                "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
            if r.status_code >= 400:
                raise LLMError(f"OpenAI returned HTTP {r.status_code}")
            return r.json()["choices"][0]["message"]["content"]
        if p == "gemini":
            return _gemini(system, user, max_tokens, timeout)
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(redact(f"{p} call failed: {type(e).__name__}"))
    raise LLMError("No LLM provider configured")


def check() -> dict:
    import time
    p = provider()
    if not p:
        return {"ok": False, "provider": None, "error": "No AI key configured"}
    t0 = time.time()
    try:
        out = complete("Reply with the single word OK.", "ping", max_tokens=200, timeout=60)
        return {"ok": True, "provider": p, "model": last_model, "seconds": round(time.time() - t0, 1),
                "reply": out.strip()[:40], "candidates": _gemini_models}
    except Exception as e:
        return {"ok": False, "provider": p, "seconds": round(time.time() - t0, 1), "error": redact(e),
                "candidates": _gemini_models}


def extract_json(text: str):
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("No JSON in model output")
    return json.loads(text[start:text.rfind("}" if text[start] == "{" else "]") + 1])
