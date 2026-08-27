"""Kravein Connect subsystem.

This package adds the local gateway, device registry, pairing flow, and
protocol definitions used by Kravein AI to reach companion devices.
"""

from .service import KraveinConnectService, get_service

__all__ = ["KraveinConnectService", "get_service"]
