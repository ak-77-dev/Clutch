import json
import os

HERE = os.path.dirname(__file__)
DATA_PATH = os.path.join(HERE, "data.json")
OUT_PATH = os.path.join(HERE, "dashboard.html")

TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{player} — Rocket League Stats</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.4/chart.umd.min.js"></script>
<style>
  body {{ font-family: system-ui, sans-serif; background: #12141a; color: #e8e8ec; margin: 0; padding: 24px; }}
  h1 {{ font-size: 20px; margin-bottom: 4px; }}
  .sub {{ color: #9098a8; margin-bottom: 24px; }}
  .cards {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 32px; }}
  .card {{ background: #1c1f28; border-radius: 10px; padding: 16px 20px; min-width: 120px; }}
  .card .num {{ font-size: 26px; font-weight: 700; }}
  .card .label {{ color: #9098a8; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
  .charts {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
  .chart-box {{ background: #1c1f28; border-radius: 10px; padding: 16px; }}
  @media (max-width: 800px) {{ .charts {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<h1>{player} — Rocket League Stats</h1>
<div class="sub">{count} matches tracked · generated from ballchasing.com</div>

<div class="cards">
  <div class="card"><div class="num">{win_rate}%</div><div class="label">Win rate</div></div>
  <div class="card"><div class="num">{avg_goals}</div><div class="label">Avg goals</div></div>
  <div class="card"><div class="num">{avg_assists}</div><div class="label">Avg assists</div></div>
  <div class="card"><div class="num">{avg_saves}</div><div class="label">Avg saves</div></div>
  <div class="card"><div class="num">{avg_score}</div><div class="label">Avg score</div></div>
  <div class="card"><div class="num">{mvps}</div><div class="label">MVPs</div></div>
</div>

<div class="charts">
  <div class="chart-box"><canvas id="perMatch"></canvas></div>
  <div class="chart-box"><canvas id="winLoss"></canvas></div>
  <div class="chart-box"><canvas id="boost"></canvas></div>
  <div class="chart-box"><canvas id="score"></canvas></div>
</div>

<script>
const matches = {matches_json};
const labels = matches.map((m, i) => `#${{i + 1}}`);

new Chart(document.getElementById('perMatch'), {{
  type: 'line',
  data: {{
    labels,
    datasets: [
      {{ label: 'Goals', data: matches.map(m => m.goals), borderColor: '#5eead4', tension: .3 }},
      {{ label: 'Assists', data: matches.map(m => m.assists), borderColor: '#a78bfa', tension: .3 }},
      {{ label: 'Saves', data: matches.map(m => m.saves), borderColor: '#fbbf24', tension: .3 }},
    ]
  }},
  options: {{ plugins: {{ title: {{ display: true, text: 'Goals / Assists / Saves per match' }} }} }}
}});

const wins = matches.filter(m => m.win).length;
new Chart(document.getElementById('winLoss'), {{
  type: 'doughnut',
  data: {{
    labels: ['Wins', 'Losses'],
    datasets: [{{ data: [wins, matches.length - wins], backgroundColor: ['#5eead4', '#f87171'] }}]
  }},
  options: {{ plugins: {{ title: {{ display: true, text: 'Win / Loss' }} }} }}
}});

new Chart(document.getElementById('boost'), {{
  type: 'line',
  data: {{
    labels,
    datasets: [{{ label: 'Avg boost amount', data: matches.map(m => m.boost_avg_amount), borderColor: '#38bdf8', tension: .3 }}]
  }},
  options: {{ plugins: {{ title: {{ display: true, text: 'Boost management per match' }} }} }}
}});

new Chart(document.getElementById('score'), {{
  type: 'bar',
  data: {{
    labels,
    datasets: [{{ label: 'Score', data: matches.map(m => m.score), backgroundColor: '#a78bfa' }}]
  }},
  options: {{ plugins: {{ title: {{ display: true, text: 'Score per match' }} }} }}
}});
</script>
</body>
</html>
"""


def avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 1) if values else 0


def main():
    if not os.path.exists(DATA_PATH):
        print("No data.json found. Run fetch_stats.py first.")
        return

    with open(DATA_PATH) as f:
        data = json.load(f)

    matches = data["matches"]
    if not matches:
        print("data.json has no matches yet. Upload some replays to ballchasing.com and re-run fetch_stats.py.")
        return

    wins = sum(1 for m in matches if m["win"])
    html = TEMPLATE.format(
        player=data["player"],
        count=len(matches),
        win_rate=round(100 * wins / len(matches)),
        avg_goals=avg([m["goals"] for m in matches]),
        avg_assists=avg([m["assists"] for m in matches]),
        avg_saves=avg([m["saves"] for m in matches]),
        avg_score=avg([m["score"] for m in matches]),
        mvps=sum(1 for m in matches if m.get("mvp")),
        matches_json=json.dumps(matches),
    )

    with open(OUT_PATH, "w") as f:
        f.write(html)

    print(f"Wrote {OUT_PATH} — open it in your browser.")


if __name__ == "__main__":
    main()
