"""Sanity checks on integration metadata."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.mer.const import DOMAIN

MANIFEST = Path("custom_components/mer/manifest.json")


def test_manifest_matches_domain() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["domain"] == DOMAIN
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] == "cloud_polling"
    assert manifest["requirements"] == []
    assert manifest["version"]
