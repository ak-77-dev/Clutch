import pytest
from fastapi.testclient import TestClient

from clutch.app import create_app


@pytest.fixture
def api(svc, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Clutch</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    return TestClient(create_app(svc, static_dir=dist))


@pytest.mark.parametrize("game", ["lol", "valorant", "rocketleague"])
def test_demo_profile_end_to_end(api, game):
    prof = api.get(f"/api/{game}/search", params={"q": "demo"}).json()
    assert prof["demo"] is True
    ov = api.get(f"/api/{game}/players/{prof['key']}/overview").json()
    assert ov["overview"]["summary"]["games"] > 100
    assert ov["profile"]["ranks"], "every game shows a rank (RL derives it from replays)"
    assert ov["overview"]["insights"]
    page = api.get(f"/api/{game}/players/{prof['key']}/matches", params={"limit": 5, "offset": 5}).json()
    assert len(page["items"]) == 5 and page["offset"] == 5
    detail = api.get(f"/api/{game}/players/{prof['key']}/matches/{page['items'][0]['id']}").json()
    assert detail["scoreboard"]


def test_match_filters(api):
    key = api.get("/api/valorant/search", params={"q": "demo"}).json()["key"]
    page = api.get(f"/api/valorant/players/{key}/matches", params={"character": "Jett", "limit": 100}).json()
    assert page["total"] > 0 and {m["character"] for m in page["items"]} == {"Jett"}


def test_errors(api):
    assert api.get("/api/lol/search", params={"q": "Faker#KR1"}).json()["error"] == "NOT_CONFIGURED"
    assert api.get("/api/nope/search", params={"q": "x"}).status_code == 404
    assert api.get("/api/lol/players/missing/overview").status_code == 404
    key = api.get("/api/lol/search", params={"q": "demo"}).json()["key"]
    assert api.get(f"/api/lol/players/{key}/matches/NA1_0").status_code == 404
    assert api.get("/api/lol/players/x/matches", params={"limit": 1000}).status_code == 422


def test_games_and_recent(api):
    games = api.get("/api/games").json()
    assert [g["id"] for g in games] == ["lol", "valorant", "rocketleague"]
    assert all(not g["configured"] for g in games)
    api.get("/api/valorant/search", params={"q": "demo"})
    assert api.get("/api/recent").json()[0]["game"] == "valorant"


def test_serves_spa(api):
    assert "Clutch" in api.get("/lol/some/deep/route").text  # client-side route -> index.html
    assert api.get("/assets/app.js").text == "console.log(1)"
    assert api.get("/api/unknown-endpoint").status_code == 404
