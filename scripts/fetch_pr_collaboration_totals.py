#!/usr/bin/env python3
"""Fetch lightweight PR collaboration totals for the PostHog 90-day window."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any


OWNER = "PostHog"
REPO = "posthog"
FULL_REPO = f"{OWNER}/{REPO}"
GRAPHQL = "https://api.github.com/graphql"


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def get_token() -> str:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or run(["gh", "auth", "token"]).stdout.strip()


def gql(token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        GRAPHQL,
        data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github+json",
        },
        method="POST",
    )
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                if payload.get("errors"):
                    raise RuntimeError(json.dumps(payload["errors"], indent=2))
                return payload["data"]
        except Exception as exc:
            if attempt == 4:
                raise
            wait = 2**attempt
            print(f"retry after {type(exc).__name__}: {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise AssertionError("unreachable")


def iter_windows(start: dt.date, end: dt.date, days: int = 7):
    cur = start
    while cur <= end:
        window_end = min(cur + dt.timedelta(days=days - 1), end)
        yield cur, window_end
        cur = window_end + dt.timedelta(days=1)


QUERY = """
query CollaborationTotals($query: String!, $cursor: String) {
  search(query: $query, type: ISSUE, first: 100, after: $cursor) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number
        url
        comments { totalCount }
        reviews { totalCount }
        reviewThreads { totalCount }
        closingIssuesReferences { totalCount }
        commits { totalCount }
      }
    }
  }
  rateLimit { cost remaining resetAt }
}
"""


def main() -> int:
    manifest = json.loads(Path("data/manifest.json").read_text(encoding="utf-8"))
    since = dt.datetime.fromisoformat(manifest["window"]["since"].replace("Z", "+00:00")).date()
    until = dt.datetime.fromisoformat(manifest["window"]["until"].replace("Z", "+00:00")).date()
    token = get_token()
    rows: dict[int, dict[str, Any]] = {}
    windows = []
    for start, end in iter_windows(since, until):
        query = f"repo:{FULL_REPO} is:pr is:merged merged:{start.isoformat()}..{end.isoformat()}"
        cursor = None
        downloaded = 0
        page = 0
        while True:
            page += 1
            data = gql(token, QUERY, {"query": query, "cursor": cursor})
            search = data["search"]
            for node in search["nodes"]:
                if not node:
                    continue
                rows[int(node["number"])] = {
                    "number": node["number"],
                    "url": node["url"],
                    "conversation_comments": node["comments"]["totalCount"],
                    "review_count": node["reviews"]["totalCount"],
                    "review_thread_count": node["reviewThreads"]["totalCount"],
                    "closing_issue_count_graphql": node["closingIssuesReferences"]["totalCount"],
                    "commit_count_graphql": node["commits"]["totalCount"],
                }
            downloaded += len(search["nodes"])
            rate = data["rateLimit"]
            print(
                f"{query} page={page} total={search['issueCount']} downloaded={downloaded} remaining={rate['remaining']}",
                file=sys.stderr,
            )
            if not search["pageInfo"]["hasNextPage"]:
                windows.append({"query": query, "total_count": search["issueCount"], "downloaded": downloaded})
                break
            cursor = search["pageInfo"]["endCursor"]
            time.sleep(0.25)
        time.sleep(0.5)

    out = {
        "source": "GitHub GraphQL search, PR-level totals only",
        "window": manifest["window"],
        "count": len(rows),
        "windows": windows,
        "rows": list(sorted(rows.values(), key=lambda r: r["number"])),
    }
    Path("data/raw/pr_collaboration_totals.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"count": len(rows), "path": "data/raw/pr_collaboration_totals.json"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
