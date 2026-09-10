import json
import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ.get("BALLCHASING_API_KEY")
PLAYER_NAME = os.environ.get("RL_PLAYER_NAME")
BASE_URL = "https://ballchasing.com/api"
MAX_REPLAYS = 50

if not API_KEY or not PLAYER_NAME:
    print("Set BALLCHASING_API_KEY and RL_PLAYER_NAME in .env (see .env.example)")
    sys.exit(1)

HEADERS = {"Authorization": API_KEY}


def list_replays():
    resp = requests.get(
        f"{BASE_URL}/replays",
        headers=HEADERS,
        params={"uploader": "me", "count": MAX_REPLAYS, "sort-by": "replay-date", "sort-dir": "desc"},
    )
    resp.raise_for_status()
    return resp.json().get("list", [])


def get_replay_detail(replay_id):
    resp = requests.get(f"{BASE_URL}/replays/{replay_id}", headers=HEADERS)
    resp.raise_for_status()
    return resp.json()


def find_player(replay, name):
    for team_color in ("blue", "orange"):
        team = replay.get(team_color)
        if not team:
            continue
        for player in team.get("players", []):
            if player.get("name", "").lower() == name.lower():
                return team_color, team, player
    return None, None, None


def main():
    print(f"Fetching up to {MAX_REPLAYS} recent replays uploaded by your ballchasing account...")
    replays = list_replays()
    if not replays:
        print("No replays found. Upload some via https://ballchasing.com/upload"
              " or install the BakkesMod auto-upload plugin, then re-run this script.")
        return

    matches = []
    for i, summary in enumerate(replays):
        replay_id = summary["id"]
        print(f"  [{i + 1}/{len(replays)}] {summary.get('replay_title') or replay_id}")
        try:
            detail = get_replay_detail(replay_id)
        except requests.HTTPError as e:
            print(f"    skipped ({e})")
            continue

        team_color, team, player = find_player(detail, PLAYER_NAME)
        if not player:
            print(f"    '{PLAYER_NAME}' not found in this replay, skipping")
            time.sleep(0.6)
            continue

        core = player.get("stats", {}).get("core", {})
        boost = player.get("stats", {}).get("boost", {})
        blue_goals = detail.get("blue", {}).get("stats", {}).get("core", {}).get("goals", 0)
        orange_goals = detail.get("orange", {}).get("stats", {}).get("core", {}).get("goals", 0)
        won = (team_color == "blue" and blue_goals > orange_goals) or (
            team_color == "orange" and orange_goals > blue_goals
        )

        matches.append(
            {
                "date": detail.get("date"),
                "playlist": detail.get("playlist_name") or detail.get("playlist_id"),
                "team": team_color,
                "win": won,
                "score": core.get("score"),
                "goals": core.get("goals"),
                "assists": core.get("assists"),
                "saves": core.get("saves"),
                "shots": core.get("shots"),
                "mvp": core.get("mvp", False),
                "boost_avg_amount": boost.get("avg_amount"),
            }
        )
        time.sleep(0.6)  # be polite to the free-tier rate limit

    matches.sort(key=lambda m: m["date"] or "")
    out_path = os.path.join(os.path.dirname(__file__), "data.json")
    with open(out_path, "w") as f:
        json.dump({"player": PLAYER_NAME, "matches": matches}, f, indent=2)

    print(f"\nSaved {len(matches)} matches to {out_path}")
    print("Now run: python build_dashboard.py")


if __name__ == "__main__":
    main()
