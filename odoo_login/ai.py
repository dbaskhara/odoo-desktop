"""The AI connection: one question in, one text out, with the provider the user chose.

Claude goes through the official Anthropic SDK. Gemini and GPT are plain HTTPS
calls in `ai_other.py`. The user's own API key is used and kept in Windows
Credential Manager; nothing is sent anywhere except to the chosen provider,
and only when the user clicks an AI action.
"""

from . import ai_other, winshell

PROVIDERS = {"claude": "Claude (Anthropic)", "gemini": "Gemini (Google)", "gpt": "GPT (OpenAI)"}
KEY_PAGES = {
    "claude": "https://platform.claude.com/settings/keys",
    "gemini": "https://aistudio.google.com/apikey",
    "gpt": "https://platform.openai.com/api-keys",
}
DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"
# Models that accept a server-side fallback when a safety check declines a request.
CLAUDE_FALLBACK_MODELS = ("claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 64000


class AIError(Exception):
    """A message that can be shown to the user as is."""


def provider_name(provider):
    return PROVIDERS.get(provider, provider)


def key_target(provider):
    return f"OdooDesktop:ai:{provider}"


def load_key(provider):
    try:
        return winshell.load_password(key_target(provider)) or ""
    except Exception:
        return ""


def save_key(provider, key):
    return bool(winshell.save_password(key_target(provider), provider, key.strip()))


def delete_key(provider):
    try:
        winshell.delete_password(key_target(provider))
    except Exception:
        pass


def _claude_client(key):
    try:
        import anthropic
    except ImportError as exc:  # the SDK is bundled with the exe; from source it must be installed
        raise AIError("The Claude connection needs the 'anthropic' package:  pip install anthropic") from exc
    return anthropic, anthropic.Anthropic(api_key=key, timeout=300.0)


def _claude_error(anthropic, exc):
    if isinstance(exc, anthropic.AuthenticationError):
        return AIError("Claude refused this API key. Check it in Settings.")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return AIError("This API key may not use that model.")
    if isinstance(exc, anthropic.NotFoundError):
        return AIError("Claude does not know that model. Load the model list again in Settings.")
    if isinstance(exc, anthropic.RateLimitError):
        return AIError("Claude is rate-limiting this key. Wait a moment and try again.")
    if isinstance(exc, anthropic.APIStatusError):
        return AIError(f"Claude answered with an error ({exc.status_code}): {exc.message}"[:300])
    if isinstance(exc, anthropic.APIConnectionError):
        return AIError("Could not reach Claude. Check your network connection.")
    return AIError(f"Claude: {exc}"[:300])


def _claude_models(key):
    anthropic, client = _claude_client(key)
    try:
        return [model.id for model in client.models.list()]
    except anthropic.APIError as exc:
        raise _claude_error(anthropic, exc) from exc


def _claude_ask(key, model, system, prompt):
    anthropic, client = _claude_client(key)
    request = {
        "model": model or DEFAULT_CLAUDE_MODEL,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        # Streaming keeps long answers clear of HTTP timeouts; only the final message is used.
        if request["model"] in CLAUDE_FALLBACK_MODELS:
            # If a safety check declines the request, Anthropic retries it on a fallback model.
            stream = client.beta.messages.stream(betas=[FALLBACK_BETA], fallbacks="default", **request)
        else:
            stream = client.messages.stream(**request)
        with stream as running:
            message = running.get_final_message()
    except anthropic.APIError as exc:
        raise _claude_error(anthropic, exc) from exc
    if message.stop_reason == "refusal":
        raise AIError("Claude declined to answer this request.")
    text = "".join(block.text for block in message.content if block.type == "text").strip()
    if not text:
        raise AIError("Claude returned an empty answer.")
    if message.stop_reason == "max_tokens":
        text += "\n\n[The answer was cut off because it was too long.]"
    return text


def list_models(provider, key):
    """Model ids this key can use, best guess of the most useful first."""
    if not key:
        raise AIError("Enter an API key first.")
    if provider == "claude":
        return _claude_models(key)
    try:
        return ai_other.list_models(provider, key)
    except ai_other.ProviderError as exc:
        raise AIError(str(exc)) from exc


def ask(provider, key, model, system, prompt):
    """Send one question and return the answer as text."""
    if not key:
        raise AIError("No API key is saved for this provider. Add one in Settings.")
    if provider == "claude":
        return _claude_ask(key, model, system, prompt)
    if not model:
        raise AIError("Choose a model in Settings first.")
    try:
        return ai_other.ask(provider, key, model, system, prompt)
    except ai_other.ProviderError as exc:
        raise AIError(str(exc)) from exc


# What the app asks ----------------------------------------------------------------

SYSTEM = (
    "You help one person handle their work in Odoo (tasks, messages, meetings). "
    "Write plainly and briefly, for a busy reader. Use only what is in the material you are given; "
    "when something is not in it, say so instead of guessing. No preamble and no closing offer."
)


def task_material(detail, description_text, messages_text):
    lines = [
        f"Task: {detail.name}",
        f"Project: {detail.project}",
        f"Stage: {detail.stage}",
        f"Deadline: {detail.deadline or 'none'}",
        f"Assigned to: {', '.join(detail.assignees) or 'nobody'}",
        "",
        "Description:",
        description_text or "(empty)",
        "",
        "Messages, oldest first:",
        messages_text or "(none)",
    ]
    return "\n".join(lines)


def prompt_summarise_task(material, me):
    return (
        f"{material}\n\n---\nI am {me}. Summarise this task in at most 6 short lines, then add a line "
        "'Expected from me:' with what I have to do next, and a line 'Open questions:' if anything is unclear."
    )


def prompt_reply(material, me, language="the language of the last message"):
    return (
        f"{material}\n\n---\nI am {me}. Draft the reply I should post next. Write it in {language}, in my voice, "
        "short and concrete. Do not invent facts, dates or commitments that are not in the material; where I must "
        "fill something in, write it in [square brackets]. Give only the text of the reply."
    )


def prompt_translate(text, language):
    return f"Translate the following into {language}. Keep names, numbers and formatting. Give only the translation.\n\n{text}"


def prompt_summarise_conversation(name, messages_text, me):
    return (
        f"Conversation with {name}, oldest first:\n{messages_text}\n\n---\nI am {me}. Summarise it in at most "
        "5 short lines, then add 'Waiting on me:' with what I still have to answer or do, or 'nothing'."
    )


def prompt_plan_day(tasks_text, extras, today):
    return (
        f"Today is {today}.\n\nMy open tasks (title | project | stage | deadline | days untouched):\n{tasks_text}\n\n"
        f"{extras}\n\n---\nPropose what I should work on today: at most 6 tasks, most important first, one line each "
        "with the reason. Then one line 'Can wait:' naming what I can safely leave. Use only tasks from the list."
    )


def prompt_weekly_report(summary_text):
    return (
        f"{summary_text}\n\n---\nTurn this into a short weekly report I can send to my manager: three parts, "
        "'Done', 'In progress', 'Blocked or late', each with a few bullet points. Keep task names as they are. "
        "Add nothing that is not in the list."
    )


def prompt_question(question, tasks_text, today):
    return (
        f"Today is {today}.\n\nMy open tasks (title | project | stage | deadline | days untouched):\n{tasks_text}\n\n"
        f"---\nQuestion: {question}\n\nAnswer from the list above only."
    )
