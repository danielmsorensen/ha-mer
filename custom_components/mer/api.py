"""Factory for the Driivz client (single patch point for tests)."""

from __future__ import annotations

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .driivz.client import DriivzDriverClient


def create_client(
    hass: HomeAssistant, username: str, password: str, base_url: str
) -> DriivzDriverClient:
    """Create a client with its own cookie jar so sessions never leak between entries."""
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
    return DriivzDriverClient(session, username, password, base_url=base_url)
