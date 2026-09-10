# rl-stat-tracker

Pulls your Rocket League match stats from ballchasing.com and builds a local HTML dashboard (goals/assists/saves trends, win rate, boost usage, score).

## Setup

1. Get an API key: log into https://ballchasing.com with your Steam/Epic/PSN account, then visit https://ballchasing.com/upload — your token is shown there.
2. Copy `.env.example` to `.env` and fill in `BALLCHASING_API_KEY` and `RL_PLAYER_NAME` (your exact in-game name).
3. Install deps: `pip install -r requirements.txt`

## Usage

This only sees replays that were actually **uploaded** to ballchasing.com. If you haven't uploaded any yet:
- Manually: drag `.replay` files (found in your Rocket League install under `Replays\`) onto https://ballchasing.com/upload
- Automatically going forward: install the BakkesMod plugin and enable its ballchasing.com auto-upload feature

Then:

```
python fetch_stats.py      # pulls your recent replays into data.json
python build_dashboard.py  # turns data.json into dashboard.html
```

Open `dashboard.html` in a browser. Re-run both scripts any time to refresh.
