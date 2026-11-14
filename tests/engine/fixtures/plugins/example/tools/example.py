"""Example plugin tools: ordinary @skill functions, nothing else to learn."""

import os

from kravein import safety
from kravein.registry import skill
from kravein.safety import Danger, Verdict


@skill(danger=Danger.SAFE)
def example_lookup(word: str) -> str:
    """Look up a word in the example dictionary.

    Args:
        word: The word to look up.
    """
    return f"{word}: an example definition"


@skill(danger=Danger.GUARDED)
def example_post(channel: str, text: str, _source: str = "local") -> str:
    """Post a message to an example channel.

    Args:
        channel: Where to post, e.g. "general".
        text: What to post.
    """
    # A state-changing plugin tool calls enforce itself, like a core skill.
    safety.enforce(Verdict(danger=Danger.GUARDED, reasons=[f"posts to '{channel}'"]),
                   f"post to '{channel}': {text[:80]}", skill="example_post", source=_source,
                   targets=safety.describe_targets(f"example-channel:{channel}"))
    log = os.environ.get("EXAMPLE_PLUGIN_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(f"{channel}|{text}\n")
    return f"posted to {channel}"
