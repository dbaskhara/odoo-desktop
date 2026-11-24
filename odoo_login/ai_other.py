"""Gemini (Google) and GPT (OpenAI) over plain HTTPS, with the user's own API key.

Kept apart from `ai.py`, which talks to Claude through the Anthropic SDK.
Only the standard library is used here.
"""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OPENAI = "https://api.openai.com/v1"
GEMINI = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT = 300
USER_AGENT = "OdooDesktop"


class ProviderError(Exception):
    """A message that can be shown to the user as is."""


def _call(name, url, headers, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(
        url, data=data, method="POST" if body is not None else "GET",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, **headers},
    )  # fmt: skip
    try:
        with urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - fixed https addresses
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = ""
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            error = payload.get("error") if isinstance(payload, dict) else None
            detail = (error.get("message") if isinstance(error, dict) else str(error or "")) or ""
        except (ValueError, OSError, AttributeError):
            pass
        if exc.code in (401, 403):
            raise ProviderError(f"{name} refused this API key. Check it in Settings.") from exc
        if exc.code == 429:
            raise ProviderError(f"{name} is rate-limiting this key, or it has no credit left. {detail}"[:300]) from exc
        raise ProviderError(f"{name} answered with an error ({exc.code}): {detail}"[:300]) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ProviderError(f"Could not reach {name}. Check your network connection.") from exc
    except ValueError as exc:
        raise ProviderError(f"{name} returned an unreadable answer.") from exc


def _openai_models(key):
    data = _call("OpenAI", f"{OPENAI}/models", {"Authorization": f"Bearer {key}"})
    ids = [item.get("id", "") for item in data.get("data", []) if isinstance(item, dict)]
    # Keep chat models; leave out embeddings, audio, image and moderation models.
    skip = ("embedding", "whisper", "tts", "dall-e", "moderation", "audio", "realtime", "image", "transcribe", "search")
    chat = [i for i in ids if (i.startswith(("gpt", "o", "chatgpt"))) and not any(word in i for word in skip)]
    return sorted(chat, reverse=True)


def _openai_ask(key, model, system, prompt):
    data = _call(
        "OpenAI",
        f"{OPENAI}/chat/completions",
        {"Authorization": f"Bearer {key}"},
        {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]},
    )
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("OpenAI returned no answer.") from exc
    if not (text or "").strip():
        raise ProviderError("OpenAI returned an empty answer.")
    return text.strip()


def _gemini_models(key):
    data = _call("Gemini", f"{GEMINI}/models?pageSize=200", {"x-goog-api-key": key})
    names = [
        str(item.get("name", "")).removeprefix("models/")
        for item in data.get("models", [])
        if isinstance(item, dict) and "generateContent" in (item.get("supportedGenerationMethods") or [])
    ]
    return sorted((name for name in names if name.startswith("gemini")), reverse=True)


def _gemini_ask(key, model, system, prompt):
    data = _call(
        "Gemini",
        f"{GEMINI}/models/{model}:generateContent",
        {"x-goog-api-key": key},
        {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        },
    )
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
    except (KeyError, IndexError, TypeError) as exc:
        reason = ""
        if isinstance(data, dict):
            reason = str((data.get("promptFeedback") or {}).get("blockReason") or "")
        raise ProviderError("Gemini returned no answer." + (f" Reason: {reason}." if reason else "")) from exc
    if not text.strip():
        raise ProviderError("Gemini returned an empty answer.")
    return text.strip()


def list_models(provider, key):
    return _gemini_models(key) if provider == "gemini" else _openai_models(key)


def ask(provider, key, model, system, prompt):
    return _gemini_ask(key, model, system, prompt) if provider == "gemini" else _openai_ask(key, model, system, prompt)
