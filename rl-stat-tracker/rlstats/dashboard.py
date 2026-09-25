"""Render the analytics report into a single self-contained HTML file."""

from __future__ import annotations

import html
import json
from importlib import resources
from pathlib import Path
from typing import Any

CHARTJS_URL = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.5.1/chart.umd.min.js"
CHARTJS_SRI = "sha512-WoViKhKD4qI2WruSZqv9+kvM4WfFhUMQCLN4QlDTt5aU56fLQy2gYoxWIqlEnXqJy/+Ac5q/hk1oWfqnMDhwMA=="


def _template() -> str:
    return resources.files("rlstats").joinpath("templates/dashboard.html").read_text(encoding="utf-8")


def fetch_chartjs() -> str:
    """Download Chart.js (for ``--offline`` dashboards that work without a network)."""
    import requests

    resp = requests.get(CHARTJS_URL, timeout=30)
    resp.raise_for_status()
    return resp.text


def render_dashboard(report: dict[str, Any], *, inline_chartjs: str | None = None) -> str:
    # Embedded as a JSON <script> block: escape "</" so no value can close the tag.
    payload = json.dumps(report, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    title = html.escape(f"{report.get('player', 'Player')} · Rocket League Performance")
    if inline_chartjs:
        chart_tag = "<script>" + inline_chartjs.replace("</script", "<\\/script") + "</script>"
    else:
        chart_tag = (
            f'<script src="{CHARTJS_URL}" integrity="{CHARTJS_SRI}" '
            'crossorigin="anonymous" referrerpolicy="no-referrer"></script>'
        )
    # Data last, so nothing inside the payload can be mistaken for a placeholder.
    return _template().replace("__CHARTJS__", chart_tag).replace("__TITLE__", title).replace("__RL_DATA__", payload)


def write_dashboard(report: dict[str, Any], path: str | Path, *, inline_chartjs: str | None = None) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_dashboard(report, inline_chartjs=inline_chartjs), encoding="utf-8")
    return out
