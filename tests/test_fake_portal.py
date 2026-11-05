"""The fake portal stays a drop-in for the real client."""

from __future__ import annotations

import inspect

from custom_components.mer.driivz.client import DriivzDriverClient
from tests.fake_portal import FakePortal

# Public on the client, but only called from inside it.
INTERNAL = {"find_charging_cards"}


def test_fake_answers_the_clients_interface() -> None:
    client_methods = {
        name: member
        for name, member in inspect.getmembers(DriivzDriverClient, inspect.iscoroutinefunction)
        if not name.startswith("_") and name not in INTERNAL
    }
    for name, method in client_methods.items():
        fake = getattr(FakePortal, name, None)
        assert fake is not None, f"FakePortal lacks {name}"
        assert list(inspect.signature(fake).parameters.values()) == list(
            inspect.signature(method).parameters.values()
        ), name
    for prop in ("logged_in", "rate_limit_remaining"):
        assert isinstance(getattr(DriivzDriverClient, prop), property)
        assert hasattr(FakePortal(), prop)
