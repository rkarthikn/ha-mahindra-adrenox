"""Shared fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from custom_components.mahindra_adrenox import api as api_module, endpoints as ep

FIXTURES = Path(__file__).parent / "fixtures"
BASE = "https://adrenox.test"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def no_request_gap(monkeypatch):
    monkeypatch.setattr(api_module, "MIN_REQUEST_GAP", 0)


@pytest.fixture
def endpoints(monkeypatch):
    """Point the (real-shaped) endpoints at a fake host for tests."""
    monkeypatch.setattr(ep, "API_BASE", BASE)
    monkeypatch.setattr(ep, "LOGIN", ("GET", "auth/login"))
    monkeypatch.setattr(ep, "TOKEN_REFRESH", ("GET", "auth/refreshToken"))
    monkeypatch.setattr(ep, "VEHICLES", ("GET", "getMyVehicleInfo"))
    monkeypatch.setattr(ep, "STATUS", ("GET", "vehicleStatus"))
    return BASE
