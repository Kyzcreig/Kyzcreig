#!/usr/bin/env python3
"""Generate README.md for the Kyzcreig profile from live GitHub data.

Sections: one-liner · Building (curated list in profile.json, descriptions pulled live)
· Contributing to (every third-party repo with a PR authored by the login, merged first,
honest merged/open counts, up to N PR titles) · Last updated footer.

Auth: GH_TOKEN / GITHUB_TOKEN env, else `gh auth token`. Only public data is read.
Exit 0 and write README.md; exit 1 on any API failure (never write a half-empty README).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE = json.loads((ROOT / "profile.json").read_text())
API = "https://api.github.com"


def token() -> str:
    for k in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(k):
            return os.environ[k]
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    except Exception as e:  # noqa: BLE001
        sys.exit(f"no GitHub token available: {e}")


TOKEN = token()


def get(path: str, params: dict | None = None) -> dict | list:
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "kyzcreig-profile-builder",
    })
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and attempt < 3:
                time.sleep(15 * (attempt + 1))
                continue
            sys.exit(f"GitHub API {e.code} on {path}: {e.read()[:200]!r}")
    sys.exit(f"GitHub API gave up on {path}")


def search_all_prs(q: str) -> list[dict]:
    items: list[dict] = []
    page = 1
    while True:
        data = get("/search/issues", {"q": q, "per_page": 100, "page": page})
        items.extend(data["items"])
        if len(items) >= data["total_count"] or not data["items"] or page >= 10:
            break
        page += 1
        time.sleep(2)  # search API: 30 req/min authenticated
    return items


def star_badge(full: str) -> str:
    return (f"[![GitHub stars](https://img.shields.io/github/stars/{full}"
            f"?style=flat&color=gold)](https://github.com/{full})")


CONV_PREFIX = re.compile(r"^(feat|fix|perf|docs|test|chore|refactor|ci|build|style)(\([^)]*\))?!?:\s*", re.I)
BRACKET_PREFIX = re.compile(r"^\[[^\]]+\]\s*")


def clean_title(t: str) -> str:
    t = BRACKET_PREFIX.sub("", CONV_PREFIX.sub("", t.strip()))
    return t[0].upper() + t[1:] if t else t


def display_name(full: str) -> str:
    names = PROFILE.get("display_names", {})
    if full in names:
        return names[full]
    return full.split("/", 1)[1]


def building_section() -> list[str]:
    lines = ["#### Building", ""]
    for entry in PROFILE["building"]:
        full = entry["repo"]
        repo = get(f"/repos/{full}")
        blurb = entry.get("blurb") or (repo.get("description") or "").strip().rstrip(".")
        lines.append(f"- **[{display_name(full)}](https://github.com/{full})** {star_badge(full)} - {blurb}")
    return lines


def contributing_section() -> list[str]:
    login = PROFILE["login"]
    excl = " ".join(f"-user:{o}" if o == login else f"-org:{o}" for o in PROFILE["own_orgs"])
    prs = search_all_prs(f"author:{login} is:pr {excl}")
    by_repo: dict[str, dict] = defaultdict(lambda: {"merged": [], "open": [], "closed": 0})
    for it in prs:
        full = it["repository_url"].rsplit("/repos/", 1)[1]
        if full in PROFILE.get("contrib_exclude", []):
            continue
        b = by_repo[full]
        if it["pull_request"].get("merged_at"):
            b["merged"].append(it)
        elif it["state"] == "open":
            b["open"].append(it)
        else:
            b["closed"] += 1
    # drop repos where nothing is merged and nothing is open (only closed-unmerged)
    rows = {k: v for k, v in by_repo.items() if v["merged"] or v["open"]}
    ordered = sorted(rows.items(), key=lambda kv: (-len(kv[1]["merged"]), -len(kv[1]["open"]), kv[0].lower()))
    n_max = PROFILE.get("contrib_max_titles", 3)
    lines = ["#### Contributing to", ""]
    for full, b in ordered:
        picks = sorted(b["merged"], key=lambda x: x["closed_at"] or "", reverse=True)[:n_max]
        if len(picks) < n_max:
            picks += sorted(b["open"], key=lambda x: x["created_at"], reverse=True)[: n_max - len(picks)]
        titles = ", ".join(clean_title(p["title"]) for p in picks)
        counts = []
        if b["merged"]:
            counts.append(f"{len(b['merged'])} merged")
        if b["open"]:
            counts.append(f"{len(b['open'])} open")
        lines.append(f"- **[{display_name(full)}](https://github.com/{full})** {star_badge(full)} - "
                     f"{titles} *({' · '.join(counts)})*")
    total_m = sum(len(v["merged"]) for v in rows.values())
    total_o = sum(len(v["open"]) for v in rows.values())
    return lines, total_m, total_o, len(rows)


def main() -> None:
    name = PROFILE["name"]
    links = " ".join(
        f"[![{k} {v['handle']}](https://img.shields.io/badge/{urllib.parse.quote(k)}-{urllib.parse.quote(v['handle'], safe='')}"
        f"-000?style=flat{'&logo=x' if k == 'X' else ''})]({v['url']})"
        for k, v in PROFILE["links"].items())
    contrib, tm, to, nrepos = contributing_section()
    out = [
        f"### Hey, I'm {name}", "", links, "",
        PROFILE["tagline"], "",
        *building_section(), "",
        *contrib, "",
        f"*{tm} merged · {to} open pull requests across {nrepos} projects. "
        f"Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d')} (auto-generated weekly by "
        f"[`scripts/build_readme.py`](scripts/build_readme.py)).*", "",
    ]
    (ROOT / "README.md").write_text("\n".join(out))
    print(f"README.md written: {len(PROFILE['building'])} building, {nrepos} contributing ({tm} merged / {to} open)")


if __name__ == "__main__":
    main()
