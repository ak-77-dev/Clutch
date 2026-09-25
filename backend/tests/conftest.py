from __future__ import annotations

import pytest

from clutch.games import default_providers
from clutch.service import Clutch
from clutch.store import Store


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch):
    for var in (
        "RIOT_API_KEY",
        "HENRIK_API_KEY",
        "BALLCHASING_API_KEY",
        "OPENDOTA_API_KEY",
        "COD_SSO_TOKEN",
        "FACEIT_API_KEY",
        "PUBG_API_KEY",
        "BRAWLSTARS_API_KEY",
        "CLASHROYALE_API_KEY",
        "OSU_CLIENT_ID",
        "OSU_CLIENT_SECRET",
        "CLUTCH_DESKTOP",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def svc():
    svc = Clutch(Store(":memory:"), default_providers())
    yield svc
    svc.store.close()
