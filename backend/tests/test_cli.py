from __future__ import annotations

import os
import sys
import types

import pytest

from clutch.cli import main
from clutch.local.config import ALREADY_RUNNING


@pytest.fixture
def fake_uvicorn(monkeypatch):
    calls = []

    class Config:
        def __init__(self, *args, **kwargs) -> None:
            self.args, self.kwargs = args, kwargs

    class Server:
        def __init__(self, config) -> None:
            self.config = config

        def run(self, sockets=None) -> None:
            calls.append(("server", self.config, sockets))

    def run(*args, **kwargs) -> None:
        calls.append(("run", args, kwargs))

    mod = types.ModuleType("uvicorn")
    mod.Config = Config
    mod.Server = Server
    mod.run = run
    monkeypatch.setitem(sys.modules, "uvicorn", mod)
    monkeypatch.delenv("CLUTCH_DESKTOP", raising=False)
    monkeypatch.delenv("CLUTCH_TOKEN", raising=False)
    return calls


def test_desktop_refuses_a_non_loopback_host():
    with pytest.raises(SystemExit):
        main(["serve", "--desktop", "--host", "0.0.0.0"])


def test_desktop_reports_an_existing_instance(monkeypatch, tmp_path):
    monkeypatch.setenv("CLUTCH_HOME", str(tmp_path))
    monkeypatch.setattr("clutch.local.config.acquire_instance_lock", lambda path: None)
    assert main(["serve", "--desktop", "--port", "12345"]) == ALREADY_RUNNING


def test_desktop_writes_the_pid_file_and_a_token(monkeypatch, tmp_path, fake_uvicorn):
    monkeypatch.setenv("CLUTCH_HOME", str(tmp_path))
    monkeypatch.setattr("clutch.local.config.acquire_instance_lock", lambda path: object())
    assert main(["serve", "--desktop", "--port", "12345"]) == 0
    pid = (tmp_path / "backend.pid").read_text(encoding="utf-8")
    assert f"{os.getpid()} 12345" in pid
    assert os.environ["CLUTCH_DESKTOP"] == "1"
    assert os.environ["CLUTCH_TOKEN"]
    assert fake_uvicorn[0][0] == "run"


def test_port_zero_binds_and_reports_the_port(monkeypatch, tmp_path, fake_uvicorn):
    monkeypatch.setenv("CLUTCH_HOME", str(tmp_path))
    monkeypatch.setattr("clutch.local.config.acquire_instance_lock", lambda path: object())
    assert main(["serve", "--desktop", "--port", "0"]) == 0
    kind, config, sockets = fake_uvicorn[0]
    assert kind == "server" and sockets
    port = config.kwargs["port"]
    assert port > 0 and f"{os.getpid()} {port}" in (tmp_path / "backend.pid").read_text(encoding="utf-8")
    sockets[0].close()


def test_db_flag_sets_the_env_var(monkeypatch, fake_uvicorn):
    assert main(["serve", "--port", "12345", "--db", "C:\\temp\\clutch.db"]) == 0
    assert os.environ["CLUTCH_DB"] == "C:\\temp\\clutch.db"
    args, kwargs = fake_uvicorn[0][1], fake_uvicorn[0][2]
    assert kwargs["port"] == 12345 and kwargs["host"] == "127.0.0.1"
    assert args == ("clutch.app:create_app",)
