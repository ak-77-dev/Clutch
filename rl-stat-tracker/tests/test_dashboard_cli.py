import csv
import json
import re

from rlstats import analytics as A
from rlstats.cli import main
from rlstats.dashboard import render_dashboard


def _embedded(html: str) -> dict:
    m = re.search(r'<script id="rl-data" type="application/json">(.*?)</script>', html, re.S)
    assert m, "data block missing"
    return json.loads(m.group(1))


def test_dashboard_embeds_report_and_no_placeholders(demo_matches):
    html = render_dashboard(A.build_report(demo_matches, "SkyReach"))
    assert "__RL_DATA__" not in html and "__CHARTJS__" not in html and "__TITLE__" not in html
    assert 'integrity="sha512-' in html  # CDN script is SRI-pinned
    assert _embedded(html)["views"]["all"]["summary"]["games"] == len(demo_matches)


def test_dashboard_escapes_script_breakouts(demo_matches):
    report = A.build_report(demo_matches[:12], "</script><script>alert(1)</script>")
    html = render_dashboard(report)
    assert "</script><script>alert(1)" not in html
    assert _embedded(html)["player"] == "</script><script>alert(1)</script>"


def test_offline_dashboard_inlines_chartjs(demo_matches):
    html = render_dashboard(A.build_report(demo_matches[:12], "x"), inline_chartjs="window.Chart=function(){}")
    assert "cdnjs" not in html.split("<style>")[0]
    assert "window.Chart=function(){}" in html


def test_cli_demo_report_export(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for var in ("BALLCHASING_API_KEY", "RL_PLAYER_ID", "RL_PLAYER_NAME", "RLSTATS_DB"):
        monkeypatch.delenv(var, raising=False)
    assert main(["demo", "--games", "80", "-o", "dash.html"]) == 0
    html = (tmp_path / "dash.html").read_text(encoding="utf-8")
    assert _embedded(html)["player"] == "SkyReach"

    assert main(["--db", "demo.db", "report"]) == 0
    out = capsys.readouterr().out
    assert "SkyReach" in out and "Insights" in out

    assert main(["--db", "demo.db", "export", "-o", "m.csv"]) == 0
    with open(tmp_path / "m.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 80 and rows[0]["playlist_id"]


def test_cli_sync_without_key_fails_cleanly(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BALLCHASING_API_KEY", raising=False)
    assert main(["sync"]) == 1
    assert "BALLCHASING_API_KEY" in capsys.readouterr().err


def test_cli_sync_detects_key_owner_and_builds_dashboard(tmp_path, capsys, monkeypatch, demo_replays):
    import rlstats.cli as cli
    from rlstats.demo import DEMO_PLAYER

    replays = {r["id"]: r for r in demo_replays[:30]}

    class FakeClient:
        def __init__(self, key):
            assert key == "k"

        def whoami(self):
            return {"steam_id": DEMO_PLAYER["id"], "name": DEMO_PLAYER["name"]}

        def iter_replays(self, **filters):
            yield from ({"id": rid} for rid in reversed(list(replays)))

        def replay(self, rid):
            return replays[rid]

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BALLCHASING_API_KEY", "k")
    for var in ("RL_PLAYER_ID", "RL_PLAYER_NAME", "RLSTATS_DB"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cli, "BallchasingClient", FakeClient)

    assert main(["sync", "-o", "d.html"]) == 0
    out = capsys.readouterr().out
    assert "Tracking the API key's owner: SkyReach" in out and "30 new replays" in out
    assert _embedded((tmp_path / "d.html").read_text(encoding="utf-8"))["views"]["all"]["summary"]["games"] == 30

    assert main(["sync", "--no-dashboard"]) == 0  # second run: nothing new
    assert "0 new replays" in capsys.readouterr().out
