"""autolunch.services.zomato package"""
from __future__ import annotations

from autolunch.services.zomato.real_mcp_client import RealZomatoMCPClient, TOKEN_FILE


def get_zomato_client():
    """
    Factory: returns RealZomatoMCPClient if OAuth tokens exist,
    otherwise falls back to the HTTP mock client.
    """
    if TOKEN_FILE.exists():
        return RealZomatoMCPClient()
    else:
        from autolunch.services.zomato.client import ZomatoMCPClient
        return ZomatoMCPClient()
