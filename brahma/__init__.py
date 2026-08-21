"""Brahma Echo — Linux-native system assistant.

A port of the original Windows desktop assistant to a Linux-first, modular
skills architecture. The package is deliberately UI-agnostic: the same core
drives the interactive terminal REPL (`brahma.cli`) and the background
systemd service (`brahma.daemon`).
"""

__version__ = "2.0.0-linux"
__all__ = ["__version__"]
