"""Share links: upload a clip to a free host and hand back a URL for Discord.

Uploads are public (anyone with the link can watch), which the UI says before
the first upload. Hosts need no account:

- catbox.moe       permanent, up to 200 MB
- litterbox        expires after 72 hours, up to 1 GB
"""

from __future__ import annotations

from pathlib import Path

import requests

HOSTS = {
    "catbox": {"url": "https://catbox.moe/user/api.php", "limit_mb": 200, "fields": {"reqtype": "fileupload"}},
    "litterbox": {
        "url": "https://litterbox.catbox.moe/resources/internals/api.php",
        "limit_mb": 1000,
        "fields": {"reqtype": "fileupload", "time": "72h"},
    },
}


class ShareError(RuntimeError):
    pass


def upload(path: Path, host: str = "catbox", session: requests.Session | None = None, timeout: float = 600) -> str:
    spec = HOSTS.get(host)
    if spec is None:
        raise ShareError(f"Unknown host: {host}")
    size_mb = path.stat().st_size / 1024 / 1024
    if size_mb > spec["limit_mb"]:
        raise ShareError(f"{size_mb:.0f} MB is over {host}'s {spec['limit_mb']} MB limit. Export a smaller copy first.")
    http = session or requests.Session()
    try:
        with open(path, "rb") as fh:
            resp = http.post(spec["url"], data=spec["fields"], files={"fileToUpload": (path.name, fh, "video/mp4")}, timeout=timeout)
    except requests.RequestException as exc:
        raise ShareError(f"Upload failed: {exc}") from exc
    link = resp.text.strip()
    if not resp.ok or not link.startswith("https://"):
        raise ShareError(f"The host refused the upload ({resp.status_code}): {link[:120]}")
    return link
