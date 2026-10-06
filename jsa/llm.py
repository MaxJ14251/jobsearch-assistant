"""LLM client.

Targets NVIDIA NIM, which speaks the OpenAI chat-completions shape, so the same
client works against any OpenAI-compatible endpoint by changing `base_url`.

Two things drive the design:

1. **Availability is unreliable.** Of 22 catalogue models probed on 2026-09-14,
   only 6 were callable on this account, and one returned 503 mid-probe. A
   single hardcoded model will fail. So callers get an ordered fallback chain
   and automatic retry.

2. **These are open-weights models in the 20B-550B range**, meaningfully weaker
   at instruction-following than frontier models. The master profile's rule is
   that nothing may be fabricated — and inventing plausible-sounding experience
   is exactly the failure mode of a smaller model. Generation is therefore
   never trusted on its own; see `jsa.verify`, which checks every produced
   claim back to a profile bullet id and rejects what it cannot trace.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

BASE_URL = os.environ.get("JSA_LLM_BASE_URL", "https://integrate.api.nvidia.com/v1")
API_KEY_ENV = "NVIDIA_API_KEY"

# Ordered by preference. The client walks this list on 404/503/timeout.
# Regenerate with `python -m jsa models`.
DEFAULT_MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3-super-120b-a12b",
    "mistralai/mistral-nemotron",
    "openai/gpt-oss-20b",
    "meta/llama-3.2-90b-vision-instruct",
    "deepseek-ai/deepseek-v4-flash-0731",
]

RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS_PER_MODEL = 2


class LLMError(RuntimeError):
    """Every model in the chain failed."""


# Called once per HTTP attempt, success or failure, with a dict of counts (no
# prompt or reply text). `cli.main` and `serve` set it to `ledger.record`;
# tests leave it None. A failing recorder never fails a model call (plan 26).
recorder: Callable[[dict[str, Any]], None] | None = None


def _record(**row: Any) -> None:
    if recorder is None:
        return
    row.setdefault("at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    try:
        recorder(row)
    except Exception:  # noqa: BLE001 - bookkeeping must not cost a draft
        logging.getLogger("jsa").debug("model-call ledger: not recorded", exc_info=True)


@dataclass
class Usage:
    """Per-call cost and latency. Logged so runs stay auditable."""

    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_s: float = 0.0
    attempts: int = 0
    fell_back: bool = False


@dataclass
class Completion:
    text: str
    usage: Usage
    raw: dict[str, Any] = field(default_factory=dict)


def api_key() -> str:
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise LLMError(
            f"{API_KEY_ENV} is not set. Get a key from build.nvidia.com and:\n"
            f'    setx {API_KEY_ENV} "nvapi-..."\n'
            "Then open a new shell. Never commit the key or paste it into chat."
        )
    return key


def _strip_reasoning(text: str) -> str:
    """Drop chain-of-thought some models emit ahead of the answer.

    nemotron-3.5-lightning prefixes replies with "Here's a thinking process:"
    and similar. Anything inside <think>...</think> goes too.
    """
    import re

    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    # Only strip a preamble that is followed by a blank line — i.e. where the
    # real answer demonstrably comes after it. Without that requirement this
    # eats the whole response when a legitimate single-paragraph answer happens
    # to open with one of these phrases.
    stripped = re.sub(
        r"^\s*(?:here'?s?\s+(?:a|my)\s+thinking\s+process|let me think|"
        r"okay,?\s+so\s+the\s+user)\b.*?\n\n",
        "",
        text,
        flags=re.S | re.I,
    )
    return (stripped or text).strip()


def complete(
    prompt: str,
    *,
    system: str | None = None,
    models: list[str] | None = None,
    max_tokens: int = 1500,
    temperature: float = 0.2,
    json_mode: bool = False,
    thinking: bool = False,
    timeout: float = 120.0,
    purpose: str = "",
) -> Completion:
    """Run a chat completion, falling back through `models` on failure.

    `purpose` names the caller in the model-call ledger (plan 26); every
    caller in jsa/ passes one, and a test holds them to it."""
    chain = models or DEFAULT_MODELS
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    headers = {
        "Authorization": f"Bearer {api_key()}",
        "Content-Type": "application/json",
    }
    errors: list[str] = []
    started = time.time()
    attempts = 0

    with httpx.Client(timeout=timeout) as client:
        for index, model in enumerate(chain):
            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            # Reasoning models (nemotron-3.5-lightning) otherwise spend the
            # whole token budget thinking and never emit an answer: 44s and no
            # JSON at 2000 tokens, vs 2.2s and clean JSON with thinking off.
            payload["chat_template_kwargs"] = {"enable_thinking": thinking}

            for attempt in range(MAX_ATTEMPTS_PER_MODEL):
                attempts += 1
                tried = time.time()
                common = {"purpose": purpose or "unspecified", "model": model,
                          "attempt": attempts, "fell_back": index > 0}
                try:
                    resp = client.post(
                        f"{BASE_URL}/chat/completions", headers=headers, json=payload
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{model}: {type(exc).__name__}")
                    _record(**common, ok=False, error_kind=type(exc).__name__,
                            latency_s=round(time.time() - tried, 2))
                    break  # transport failure — move to the next model

                if resp.status_code == 200:
                    body = resp.json()
                    text = body["choices"][0]["message"]["content"] or ""
                    u = body.get("usage") or {}
                    _record(**common, ok=True, error_kind=None,
                            latency_s=round(time.time() - tried, 2),
                            prompt_tokens=int(u.get("prompt_tokens") or 0),
                            completion_tokens=int(u.get("completion_tokens") or 0),
                            total_tokens=int(u.get("total_tokens") or 0))
                    return Completion(
                        text=_strip_reasoning(text),
                        usage=Usage(
                            model=model,
                            prompt_tokens=u.get("prompt_tokens", 0),
                            completion_tokens=u.get("completion_tokens", 0),
                            total_tokens=u.get("total_tokens", 0),
                            latency_s=round(time.time() - started, 2),
                            attempts=attempts,
                            fell_back=index > 0,
                        ),
                        raw=body,
                    )

                errors.append(f"{model}: {resp.status_code} {resp.text[:90]}")
                _record(**common, ok=False, error_kind=f"http {resp.status_code}",
                        latency_s=round(time.time() - tried, 2))
                if resp.status_code in RETRY_STATUS:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break  # 404/401 etc. — this model won't work, try the next

    raise LLMError(
        "every model in the chain failed:\n  " + "\n  ".join(errors)
    )


JSON_SYSTEM = (
    "You output raw JSON and nothing else. No prose, no markdown fences, no "
    "explanation. The response must begin with { or [ and be valid JSON."
)


def _balanced_objects(text: str) -> list[str]:
    """Every balanced {...} span in `text`, in order, ignoring braces in strings."""
    spans: list[str] = []
    depth = start = 0
    in_str = escaped = False
    for i, ch in enumerate(text):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                spans.append(text[start : i + 1])
    return spans


def extract_json(text: str, expect: type | None = dict) -> Any:
    """Parse JSON out of a model response, or raise json.JSONDecodeError.

    Two hard-won rules, from watching nemotron-3.5-lightning on 2026-09-14:

    1. **Take the LAST balanced object, not the first.** Reasoning models echo
       the requested schema while thinking, so the first `{...}` in the stream
       is usually the prompt's own template, not an answer.

    2. **Honour `expect`.** A naive "first brace to last brace" scan pulled
       `["up to 5 primary technologies"]` straight out of an echoed template
       and returned it as a successful parse. That is worse than failing: it is
       confident, well-formed, wrong data flowing downstream. When an object is
       expected, an array is a parse failure.
    """
    text = (text or "").strip()

    def check(value: Any) -> Any:
        if expect is not None and not isinstance(value, expect):
            raise json.JSONDecodeError(
                f"expected {expect.__name__}, got {type(value).__name__}", text[:200], 0
            )
        return value

    try:
        return check(json.loads(text))
    except json.JSONDecodeError:
        pass

    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            body = parts[1]
            if body[:4].lower().startswith("json"):
                body = body.split("\n", 1)[-1]
            try:
                return check(json.loads(body.strip()))
            except json.JSONDecodeError:
                pass

    for span in reversed(_balanced_objects(text)):
        try:
            return check(json.loads(span))
        except json.JSONDecodeError:
            continue

    if expect in (None, list):
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end > start:
            try:
                return check(json.loads(text[start : end + 1]))
            except json.JSONDecodeError:
                pass

    raise json.JSONDecodeError("no valid JSON found", text[:200], 0)


def complete_json(prompt: str, *, attempts: int = 3, **kw: Any) -> tuple[Any, Usage]:
    """Completion that must return JSON, retrying on malformed output.

    Observed on 2026-09-14: nemotron-3-ultra returned `{"ok":{ "provider":
    "nim" }` — unbalanced and unparseable — *with* response_format set to
    json_object. Structured-output guarantees do not hold on this tier, so a
    single bad parse must not kill a run. Each retry raises the temperature
    floor slightly and restates the constraint.
    """
    last_text = ""
    last_usage = Usage()
    # The caller's system prompt on EVERY attempt: retries used to fall back
    # to JSON_SYSTEM and lose rules such as "never mention a degree" (R-22).
    system = kw.pop("system", None)
    for attempt in range(attempts):
        result = complete(
            prompt if attempt == 0 else f"{prompt}\n\nReturn ONLY valid JSON.",
            system=system or JSON_SYSTEM,
            json_mode=True,
            **kw,
        )
        last_text, last_usage = result.text, result.usage
        try:
            return extract_json(result.text), result.usage
        except json.JSONDecodeError:
            continue
    raise LLMError(
        f"model returned unparseable JSON after {attempts} attempts "
        f"(last model: {last_usage.model}): {last_text[:300]}"
    )


def probe_models(candidates: list[str] | None = None) -> list[tuple[str, bool, str]]:
    """Check which models this account can actually call.

    The /models catalogue lists far more than a given account may invoke —
    most return 404 'Not found for account'.
    """
    chain = candidates or DEFAULT_MODELS
    headers = {"Authorization": f"Bearer {api_key()}"}
    out = []
    with httpx.Client(timeout=60.0) as client:
        for model in chain:
            try:
                tried = time.time()
                r = client.post(
                    f"{BASE_URL}/chat/completions",
                    headers=headers,
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "Say OK."}],
                        "max_tokens": 10,
                    },
                )
                _record(purpose="probe", model=model, attempt=1, fell_back=False,
                        ok=r.status_code == 200,
                        error_kind=None if r.status_code == 200 else f"http {r.status_code}",
                        latency_s=round(time.time() - tried, 2))
                out.append(
                    (model, r.status_code == 200,
                     "ok" if r.status_code == 200 else f"{r.status_code} {r.text[:60]}")
                )
            except Exception as exc:  # noqa: BLE001
                out.append((model, False, type(exc).__name__))
    return out
