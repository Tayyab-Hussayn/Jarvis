"""Inspecting and switching the LLM backend."""

from __future__ import annotations

from .. import config
from ..registry import skill
from ..safety import Danger

KNOWN_PROVIDERS = ("cline", "gemini", "openrouter")


def _gemini_report() -> str:
    """Describe the Gemini backend without revealing the key."""
    from ..llm import GeminiProvider

    provider = GeminiProvider()
    lines = ["Gemini provider"]

    key = config.gemini_api_key()
    if not key:
        lines.append("  key      : NOT SET")
        lines.append(
            f"  Fix      : add gemini_api_key to "
            f"{config.CONFIG_DIR / 'api_keys.json'}, or set KRAVEIN_GEMINI_API_KEY"
        )
        return "\n".join(lines)

    lines.append(f"  key      : present ({len(key)} chars, ends …{key[-4:]})")
    lines.append(f"  model    : {provider.model_name()}")
    lines.append(f"  available: {'yes' if provider.available() else 'no'}")

    order = [str(n).lower() for n in (config.get("llm.provider_order") or [])]
    if order and "gemini" not in order:
        lines.append(
            "  role     : voice (Live native-audio) and vision only — "
            "kept out of the text chain because it proved unreliable here"
        )
    else:
        lines.append(f"  timeout  : {config.get('llm.gemini_timeout_seconds', 20)}s, then failover")
    return "\n".join(lines)


@skill(category="llm", danger=Danger.SAFE)
def llm_status() -> str:
    """Show which LLM provider and model the assistant is using."""
    from .. import cline
    from ..llm import client

    return "\n\n".join([client().status(), _gemini_report(), cline.doctor()])


@skill(category="llm", danger=Danger.MODERATE)
def reset_llm_failover() -> str:
    """Clear provider cooldowns so the primary model is tried again.

    After repeated failures a provider is skipped for a few minutes. Use this
    to retry immediately once you believe it has recovered.
    """
    from ..llm import client

    facade = client()
    before = facade.breaker_status()
    facade.reset_breaker()
    return f"Was: {before}\nNow: {facade.status()}"


@skill(category="llm", danger=Danger.SAFE)
def list_llm_models(filter_text: str = "") -> str:
    """List models available through the Cline account.

    Args:
        filter_text: Only show models whose id contains this text.
    """
    import json

    from ..platform import shell

    if not shell.have("cline"):
        return "The cline CLI is not installed."

    # `cline config --json` reports the resolved provider/model settings.
    result = shell.run(["cline", "config", "--json"], timeout=45)
    if result.ok and result.stdout.strip():
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            data = None
        if data:
            rendered = json.dumps(data, indent=2)
            if filter_text:
                kept = [l for l in rendered.splitlines() if filter_text.lower() in l.lower()]
                return "\n".join(kept) or f"Nothing matching '{filter_text}'."
            return rendered

    return (
        "Could not read the model list from the CLI.\n"
        "Browse and switch models interactively with:  cline auth"
    )


@skill(category="llm", danger=Danger.MODERATE)
def set_llm_model(model: str) -> str:
    """Change which model the assistant uses through Cline.

    Args:
        model: A model id, e.g. 'deepseek/deepseek-v4-flash'.
    """
    model = (model or "").strip()
    if not model:
        return "Give a model id."

    settings = config.load_settings()
    settings.setdefault("llm", {})["cline_model"] = model
    config.save_settings(settings)

    from .. import cline

    return (
        f"Assistant model set to '{model}'.\n"
        f"Now using: {cline.configured_model()}\n"
        "This only affects Kravein; your interactive `cline` sessions are unchanged."
    )


@skill(category="llm", danger=Danger.MODERATE)
def set_llm_provider(provider: str) -> str:
    """Choose which LLM backend the assistant prefers.

    Args:
        provider: 'cline', 'gemini' or 'openrouter'.
    """
    provider = (provider or "").strip().lower()
    if provider not in KNOWN_PROVIDERS:
        return f"Unknown provider '{provider}'. Choose from: {', '.join(KNOWN_PROVIDERS)}."

    from ..llm import client

    available = client().providers()
    if provider not in available:
        detail = ", ".join(available) or "none"
        hint = {
            "cline": "Install the CLI and run:  cline auth",
            "gemini": "Add gemini_api_key to your api_keys.json.",
            "openrouter": "Add openrouter_api_key to your api_keys.json.",
        }[provider]
        return (
            f"'{provider}' is not usable right now (available: {detail}).\n{hint}"
        )

    settings = config.load_settings()
    settings.setdefault("llm", {})["provider"] = provider
    config.save_settings(settings)

    # The facade caches provider order, so rebuild it.
    import kravein.llm as llm_module

    llm_module._client = None

    return f"LLM provider set to '{provider}'.\n{llm_module.client().status()}"
