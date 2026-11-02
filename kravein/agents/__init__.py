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
from . import proposals  # noqa: E402,F401  (registers propose / decide_proposal)
from . import tasks as tasks  # noqa: E402  (registers task_status / cancel_task)
