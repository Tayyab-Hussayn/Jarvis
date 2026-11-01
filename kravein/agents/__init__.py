"""Agent profiles and their persistent history. See ``profile.py``."""

from .profile import (  # noqa: F401
    AgentProfile,
    ProfileError,
    list_profiles,
    load_profile,
    parse_profile,
)

# Registers the `delegate` skill (category "agents", hidden from agents that
# have no profile; see registry.tool_declarations).
from . import delegate  # noqa: E402,F401
