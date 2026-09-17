"""Shared helpers for tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mer.driivz.models import Station

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    """Return the text of a fixture file."""
    return (FIXTURES / name).read_text(encoding="utf-8")


def load_json_fixture(name: str) -> Any:
    """Return the parsed JSON of a fixture file."""
    return json.loads(load_fixture(name))


def station_from_fixture(name: str) -> Station:
    """Build a Station from a `findStationById` fixture."""
    return Station.from_dict(load_json_fixture(name)["data"])


def stations_from_fixture(name: str) -> list[Station]:
    """Build Stations from a list-returning fixture."""
    return [Station.from_dict(item) for item in load_json_fixture(name)["data"]]


async def setup_integration(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add the entry to hass and set it up."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
