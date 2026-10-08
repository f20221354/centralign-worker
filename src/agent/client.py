"""LLM access: one OpenAI-compatible code path, several providers, failover and free-tier pacing."""
from __future__ import annotations

import json
import os
import time
from typing import Any

import openai

# name: (base_url, api-key env var, model env var, default model)
PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "GROQ_MODEL", "openai/gpt-oss-120b"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", "GEMINI_MODEL",
               "gemini-2.5-flash"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", "OPENAI_MODEL", "gpt-4.1-mini"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY", "MISTRAL_MODEL", "mistral-small-latest"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "OPENROUTER_MODEL",
                   "openai/gpt-oss-120b:free"),
    "ollama": (os.environ.get("OLLAMA_URL", "http://localhost:11434/v1"), None, "OLLAMA_MODEL", "qwen2.5:7b"),
}
MIN_GAP = {"groq": 2.0, "gemini": 6.5, "mistral": 1.2, "openrouter": 3.2}  # stay under free-tier RPM limits
COOLDOWN_S, MAX_WAIT_S, RATE_BUDGET_S = 60, 30, 600
USAGE: dict[str, Any] = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "providers": {}}
_last: dict[str, float] = {}
_cooldown: dict[str, float] = {}


def chain() -> list[str]:
    names = [n.strip() for n in os.environ.get("LLM_CHAIN", "groq,gemini,openai,mistral,openrouter").split(",")]
    return [n for n in names if n in PROVIDERS and ((key := PROVIDERS[n][1]) is None or os.environ.get(key))]


def model_of(name: str) -> str:
    return os.environ.get(PROVIDERS[name][2], PROVIDERS[name][3])


def _call(name: str, messages: list[Any], tools: list[Any] | None) -> Any:
    gap = MIN_GAP.get(name, 0) - (time.time() - _last.get(name, 0))
    if gap > 0:
        time.sleep(gap)
    _last[name] = time.time()
    base, key_env, *_ = PROVIDERS[name]
    client = openai.OpenAI(base_url=base, api_key=os.environ.get(key_env, "") if key_env else "ollama",
                           max_retries=0, timeout=120)
    kw: dict[str, Any] = {"tools": tools, "tool_choice": "auto"} if tools else {}
    return client.chat.completions.create(model=model_of(name), messages=messages, temperature=0, **kw)


def complete(messages: list[dict], tools: list[dict] | None = None) -> tuple[Any, str]:
    """First provider that answers wins. Rate limits are waited out (free tiers); hard errors fail over."""
    names = chain()
    if not names:
        raise RuntimeError("no LLM provider configured: set GROQ_API_KEY, GEMINI_API_KEY or OPENAI_API_KEY")
    waited, errors = 0.0, []
    while True:
        now, hard, hints = time.time(), False, []
        ready = [n for n in names if _cooldown.get(n, 0) <= now] or names
        for name in ready:
            for attempt in range(3):
                try:
                    r = _call(name, messages, tools)
                    USAGE["calls"] += 1
                    USAGE["input_tokens"] += getattr(r.usage, "prompt_tokens", 0) or 0
                    USAGE["output_tokens"] += getattr(r.usage, "completion_tokens", 0) or 0
                    USAGE["providers"][name] = USAGE["providers"].get(name, 0) + 1
                    return r.choices[0].message, f"{name}:{model_of(name)}"
                except openai.RateLimitError as e:
                    if "insufficient_quota" in str(e) or "credit" in str(e).lower():
                        errors.append(f"{name}: out of credits")
                        _cooldown[name] = time.time() + 3600
                        break
                    try:
                        wait: float | None = float(e.response.headers.get("retry-after") or "")
                    except ValueError:
                        wait = None
                    if wait is not None:
                        hints.append(wait)
                    if wait is not None and wait <= MAX_WAIT_S and attempt < 2:
                        time.sleep(wait + 0.5)
                        continue
                    errors.append(f"{name}: rate limited")
                    break
                except (openai.APIConnectionError, openai.InternalServerError, openai.APITimeoutError) as e:
                    errors.append(f"{name}: {type(e).__name__}")
                    hard = True
                    time.sleep(2 ** attempt)
                except openai.APIStatusError as e:
                    errors.append(f"{name}: HTTP {e.status_code} {str(e.message)[:120]}")
                    hard = True
                    break
            _cooldown[name] = max(_cooldown.get(name, 0), time.time() + COOLDOWN_S)
        if hard or waited >= RATE_BUDGET_S:
            raise RuntimeError("every LLM provider failed: " + "; ".join(errors[-6:]))
        pause = min(max([20.0, *hints]), 90.0)
        time.sleep(pause)
        waited += pause
        _cooldown.clear()


def complete_json(prompt: str, system: str = "Reply with one JSON object only.") -> dict[str, Any]:
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    last = ""
    for _ in range(2):
        msg, _provider = complete(msgs)
        last = msg.content or ""
        try:
            return json.loads(last[last.index("{"): last.rindex("}") + 1])
        except ValueError:
            msgs += [{"role": "assistant", "content": last}, {"role": "user", "content": "That was not valid JSON. "
                                                                                        "Reply with the JSON object only."}]
    raise ValueError(f"model did not return JSON: {last[:200]}")
