#!/usr/bin/env python3
"""
Fetch source data for a PostHog engineering-impact analysis.

The goal of this script is to build a reproducible local dataset, not to decide
the final impact model. It collects broad GitHub evidence for the last N days:

- complete merged PR metadata via GraphQL
- PR review identity samples and totals via GraphQL
- closed and recently updated issues via REST search
- repository/release/workflow metadata via REST
- complete master-branch commit and file-change history via a shallow blobless
  git clone, which avoids one REST call per PR file list
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


OWNER = "PostHog"
REPO = "posthog"
FULL_REPO = f"{OWNER}/{REPO}"
GITHUB_API = "https://api.github.com"
GITHUB_GRAPHQL = "https://api.github.com/graphql"
DEFAULT_DAYS = 90


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def isoformat_z(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=check)


def get_gh_token() -> str:
    env_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if env_token:
        return env_token.strip()
    proc = run(["gh", "auth", "token"])
    return proc.stdout.strip()


class GitHubClient:
    def __init__(self, token: str, pause_seconds: float = 0.05) -> None:
        self.token = token
        self.pause_seconds = pause_seconds

    def _request(self, url: str, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
        data = None
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "posthog-impact-data-fetcher",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    raw = resp.read().decode("utf-8")
                    if self.pause_seconds:
                        time.sleep(self.pause_seconds)
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                if exc.code in {403, 429, 502, 503, 504} and attempt < 4:
                    wait = 2**attempt
                    print(f"Retrying {url} after HTTP {exc.code}; wait={wait}s", file=sys.stderr)
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"GitHub request failed: {exc.code} {url}\n{body}") from exc

    def rest(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not path.startswith("/"):
            path = "/" + path
        url = GITHUB_API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return self._request(url)

    def rest_paginated(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        max_pages: int | None = None,
    ) -> list[Any]:
        params = dict(params or {})
        params.setdefault("per_page", 100)
        page = 1
        out: list[Any] = []
        while True:
            params["page"] = page
            data = self.rest(path, params)
            if not data:
                break
            if isinstance(data, dict) and "items" in data:
                batch = data["items"]
            elif isinstance(data, list):
                batch = data
            else:
                raise TypeError(f"Unexpected paginated response for {path}: {type(data)}")
            out.extend(batch)
            if len(batch) < int(params["per_page"]):
                break
            page += 1
            if max_pages and page > max_pages:
                break
        return out

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        payload = {"query": query, "variables": variables}
        data = self._request(GITHUB_GRAPHQL, method="POST", payload=payload)
        if data.get("errors"):
            raise RuntimeError(json.dumps(data["errors"], indent=2))
        return data["data"]


MERGED_PRS_QUERY = """
query MergedPullRequestsBySearch($query: String!, $cursor: String) {
  search(query: $query, type: ISSUE, first: 100, after: $cursor) {
    issueCount
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      ... on PullRequest {
        number
        title
        url
        state
        createdAt
        updatedAt
        closedAt
        mergedAt
        baseRefName
        headRefName
        isCrossRepository
        additions
        deletions
        changedFiles
        author {
          login
        }
        mergedBy {
          login
        }
        commits {
          totalCount
        }
        comments {
          totalCount
        }
        reviews {
          totalCount
        }
        reviewThreads {
          totalCount
        }
        labels(first: 20) {
          nodes {
            name
            color
            description
          }
        }
        closingIssuesReferences {
          totalCount
        }
      }
    }
  }
  rateLimit {
    limit
    cost
    remaining
    resetAt
  }
}
"""

OLD_MERGED_PRS_QUERY = """
query MergedPullRequests($owner: String!, $repo: String!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequests(first: 50, after: $cursor, states: MERGED, orderBy: {field: UPDATED_AT, direction: DESC}) {
      pageInfo {
        hasNextPage
        endCursor
      }
      nodes {
        number
        title
        url
        state
        createdAt
        updatedAt
        closedAt
        mergedAt
        baseRefName
        headRefName
        isCrossRepository
        additions
        deletions
        changedFiles
        bodyText
        author {
          login
          ... on User {
            name
            company
          }
        }
        mergedBy {
          login
        }
        commits {
          totalCount
        }
        comments {
          totalCount
        }
        reviews {
          totalCount
        }
        reviewThreads {
          totalCount
        }
        labels(first: 20) {
          nodes {
            name
            color
            description
          }
        }
        assignees(first: 10) {
          nodes {
            login
          }
        }
        closingIssuesReferences(first: 10) {
          totalCount
          nodes {
            number
            title
            url
            state
          }
        }
      }
    }
  }
  rateLimit {
    limit
    cost
    remaining
    resetAt
  }
}
"""


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def iter_date_windows(start: dt.date, end: dt.date, days: int = 7) -> list[tuple[dt.date, dt.date]]:
    windows: list[tuple[dt.date, dt.date]] = []
    cur = start
    while cur <= end:
        window_end = min(cur + dt.timedelta(days=days - 1), end)
        windows.append((cur, window_end))
        cur = window_end + dt.timedelta(days=1)
    return windows


def fetch_merged_prs_for_query(client: GitHubClient, query: str) -> tuple[int, list[dict[str, Any]]]:
    cursor = None
    out: list[dict[str, Any]] = []
    page = 0
    issue_count = 0
    while True:
        page += 1
        data = client.graphql(MERGED_PRS_QUERY, {"query": query, "cursor": cursor})
        conn = data["search"]
        issue_count = conn["issueCount"]
        nodes = conn["nodes"]
        for node in nodes:
            if node and node.get("mergedAt"):
                out.append(node)
        rate = data["rateLimit"]
        print(
            f"GraphQL search page {page}: query={query!r} issueCount={issue_count} "
            f"batch={len(nodes)} total={len(out)} remaining={rate['remaining']}",
            file=sys.stderr,
        )
        if not conn["pageInfo"]["hasNextPage"]:
            break
        cursor = conn["pageInfo"]["endCursor"]
    return issue_count, out


def fetch_merged_prs(client: GitHubClient, since: dt.datetime, until: dt.datetime) -> list[dict[str, Any]]:
    # Date-bucketed search avoids scanning the repository's full PR history and
    # avoids GitHub Search's 1,000-result cap per query.
    all_prs: dict[int, dict[str, Any]] = {}
    windows = iter_date_windows(since.date(), until.date(), days=7)
    for start_date, end_date in windows:
        query = (
            f"repo:{FULL_REPO} is:pr is:merged "
            f"merged:{start_date.isoformat()}..{end_date.isoformat()}"
        )
        issue_count, prs = fetch_merged_prs_for_query(client, query)
        if issue_count > 1000 and start_date < end_date:
            print(f"Splitting oversized window {start_date}..{end_date} ({issue_count})", file=sys.stderr)
            for sub_start, sub_end in iter_date_windows(start_date, end_date, days=1):
                sub_query = (
                    f"repo:{FULL_REPO} is:pr is:merged "
                    f"merged:{sub_start.isoformat()}..{sub_end.isoformat()}"
                )
                _, sub_prs = fetch_merged_prs_for_query(client, sub_query)
                for pr in sub_prs:
                    all_prs[int(pr["number"])] = pr
        else:
            for pr in prs:
                all_prs[int(pr["number"])] = pr
    out = list(all_prs.values())
    out.sort(key=lambda x: x.get("mergedAt") or "")
    return out


def rest_search_all(
    client: GitHubClient,
    query: str,
    sort: str | None = None,
    order: str | None = None,
    max_items: int | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"q": query, "per_page": 100}
    if sort:
        params["sort"] = sort
    if order:
        params["order"] = order
    total = client.rest("/search/issues", {**params, "per_page": 1}).get("total_count", 0)
    page_cap = 10 if total > 1000 else None
    items = client.rest_paginated("/search/issues", params, max_pages=page_cap)
    if max_items is not None:
        items = items[:max_items]
    return {"query": query, "total_count": total, "downloaded_count": len(items), "items": items}


def rest_search_issue_windows(
    client: GitHubClient,
    search_terms: str,
    date_field: str,
    since: dt.datetime,
    until: dt.datetime,
) -> dict[str, Any]:
    all_items: dict[int, dict[str, Any]] = {}
    window_counts: list[dict[str, Any]] = []
    for start_date, end_date in iter_date_windows(since.date(), until.date(), days=7):
        query = f"{search_terms} {date_field}:{start_date.isoformat()}..{end_date.isoformat()}"
        total, items = rest_search_issue_items(client, query)
        if total > 1000 and start_date < end_date:
            for sub_start, sub_end in iter_date_windows(start_date, end_date, days=1):
                sub_query = f"{search_terms} {date_field}:{sub_start.isoformat()}..{sub_end.isoformat()}"
                sub_total, sub_items = rest_search_issue_items(client, sub_query)
                window_counts.append(
                    {
                        "query": sub_query,
                        "total_count": sub_total,
                        "downloaded_count": len(sub_items),
                    }
                )
                for item in sub_items:
                    all_items[int(item["number"])] = item
        else:
            window_counts.append({"query": query, "total_count": total, "downloaded_count": len(items)})
            for item in items:
                all_items[int(item["number"])] = item
    items = list(all_items.values())
    items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return {
        "query": f"{search_terms} {date_field}:{since.date().isoformat()}..{until.date().isoformat()}",
        "window_counts": window_counts,
        "downloaded_count": len(items),
        "items": items,
    }


def rest_search_issue_items(client: GitHubClient, query: str) -> tuple[int, list[dict[str, Any]]]:
    out: list[dict[str, Any]] = []
    page = 1
    total = 0
    while True:
        data = client.rest(
            "/search/issues",
            {"q": query, "sort": "updated", "order": "desc", "per_page": 100, "page": page},
        )
        total = data.get("total_count", total)
        batch = data.get("items", [])
        out.extend(batch)
        print(
            f"REST search page {page}: query={query!r} total={total} batch={len(batch)} downloaded={len(out)}",
            file=sys.stderr,
        )
        if len(batch) < 100 or page >= 10:
            time.sleep(2.2)
            break
        # GitHub's REST search endpoint has a much lower per-minute limit.
        time.sleep(2.2)
        page += 1
    return total, out


def search_item_to_pr(item: dict[str, Any]) -> dict[str, Any]:
    pull_request = item.get("pull_request") or {}
    labels = item.get("labels") or []
    return {
        "number": item.get("number"),
        "title": item.get("title"),
        "url": item.get("html_url"),
        "state": item.get("state", "").upper(),
        "createdAt": item.get("created_at"),
        "updatedAt": item.get("updated_at"),
        "closedAt": item.get("closed_at"),
        "mergedAt": pull_request.get("merged_at"),
        "baseRefName": None,
        "headRefName": None,
        "isCrossRepository": None,
        "additions": None,
        "deletions": None,
        "changedFiles": None,
        "bodyText": item.get("body") or "",
        "author": {"login": (item.get("user") or {}).get("login")},
        "mergedBy": {"login": None},
        "commits": {"totalCount": None},
        "comments": {"totalCount": item.get("comments")},
        "reviews": {"totalCount": None},
        "reviewThreads": {"totalCount": None},
        "labels": {
            "nodes": [
                {
                    "name": label.get("name"),
                    "color": label.get("color"),
                    "description": label.get("description"),
                }
                for label in labels
            ]
        },
        "closingIssuesReferences": {"totalCount": None},
        "search_score": item.get("score"),
        "raw_issue_api_url": item.get("url"),
        "raw_pull_api_url": pull_request.get("url"),
    }


def fetch_merged_prs_rest(client: GitHubClient, since: dt.datetime, until: dt.datetime) -> list[dict[str, Any]]:
    all_prs: dict[int, dict[str, Any]] = {}
    for start_date, end_date in iter_date_windows(since.date(), until.date(), days=7):
        query = (
            f"repo:{FULL_REPO} is:pr is:merged "
            f"merged:{start_date.isoformat()}..{end_date.isoformat()}"
        )
        total, items = rest_search_issue_items(client, query)
        if total > 1000 and start_date < end_date:
            print(f"Splitting oversized REST search window {start_date}..{end_date} ({total})", file=sys.stderr)
            for sub_start, sub_end in iter_date_windows(start_date, end_date, days=1):
                sub_query = (
                    f"repo:{FULL_REPO} is:pr is:merged "
                    f"merged:{sub_start.isoformat()}..{sub_end.isoformat()}"
                )
                _, sub_items = rest_search_issue_items(client, sub_query)
                for item in sub_items:
                    pr = search_item_to_pr(item)
                    all_prs[int(pr["number"])] = pr
        else:
            for item in items:
                pr = search_item_to_pr(item)
                all_prs[int(pr["number"])] = pr
    out = list(all_prs.values())
    out.sort(key=lambda x: x.get("mergedAt") or "")
    return out


def ensure_posthog_git_clone(target_dir: Path, depth: int = 12000) -> None:
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if (target_dir / ".git").exists():
        print("Updating existing PostHog shallow clone", file=sys.stderr)
        run(["git", "fetch", "--depth", str(depth), "origin", "master"], cwd=target_dir)
        run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=target_dir)
        return
    print("Cloning PostHog repository as shallow blobless checkout", file=sys.stderr)
    run(
        [
            "git",
            "clone",
            "--filter=blob:none",
            "--no-checkout",
            "--single-branch",
            "--branch",
            "master",
            "--depth",
            str(depth),
            "https://github.com/PostHog/posthog.git",
            str(target_dir),
        ]
    )
    run(["git", "checkout", "-q", "master"], cwd=target_dir)


PR_NUMBER_RE = re.compile(r"\(#(?P<number>\d+)\)\s*$")


def classify_path(path: str) -> str:
    p = path.strip()
    if not p:
        return "unknown"
    lower = p.lower()
    if lower.startswith(("frontend/", "ee/frontend/")) or lower.endswith((".tsx", ".jsx", ".css", ".scss")):
        return "frontend"
    if lower.startswith(("posthog/", "ee/", "products/", "common/", "rust/", "plugin-server/")):
        return "product_backend"
    if lower.startswith(("migrations/", "posthog/migrations/", "ee/migrations/")):
        return "database_migration"
    if lower.startswith((".github/", "bin/", "scripts/", "docker", "docker-compose", "infra/", "helm/", "charts/")):
        return "infra_ci"
    if lower.startswith(("docs/", "contents/", "readme")) or lower.endswith((".md", ".mdx", ".rst")):
        return "docs"
    if "test" in lower or lower.endswith((".spec.ts", ".test.ts", ".spec.tsx", ".test.tsx", "_test.py")):
        return "tests"
    return "other"


def parse_git_log(repo_dir: Path, since: dt.datetime, until: dt.datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fmt = "%x1e%H%x1f%an%x1f%ae%x1f%aI%x1f%cn%x1f%ce%x1f%cI%x1f%s"
    proc = run(
        [
            "git",
            "log",
            "HEAD",
            f"--since={isoformat_z(since)}",
            f"--until={isoformat_z(until)}",
            "--date=iso-strict",
            f"--pretty=format:{fmt}",
            "--name-only",
            "--no-renames",
        ],
        cwd=repo_dir,
    )
    chunks = [chunk for chunk in proc.stdout.split("\x1e") if chunk.strip()]
    commits: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    for chunk in chunks:
        lines = chunk.splitlines()
        if not lines:
            continue
        header = lines[0].split("\x1f")
        if len(header) < 8:
            continue
        sha, author_name, author_email, authored_at, committer_name, committer_email, committed_at, subject = header[:8]
        pr_match = PR_NUMBER_RE.search(subject.strip())
        pr_number = int(pr_match.group("number")) if pr_match else None
        additions = None
        deletions = None
        file_count = 0
        categories: Counter[str] = Counter()
        for line in lines[1:]:
            path = line.strip()
            if not path:
                continue
            category = classify_path(path)
            files.append(
                {
                    "sha": sha,
                    "pr_number": pr_number,
                    "path": path,
                    "category": category,
                    "additions": None,
                    "deletions": None,
                    "is_binary": None,
                }
            )
            file_count += 1
            categories[category] += 1
        commits.append(
            {
                "sha": sha,
                "author_name": author_name,
                "author_email": author_email,
                "authored_at": authored_at,
                "committer_name": committer_name,
                "committer_email": committer_email,
                "committed_at": committed_at,
                "subject": subject,
                "pr_number": pr_number,
            "additions": additions,
            "deletions": deletions,
                "file_count": file_count,
                "file_categories": dict(categories),
            }
        )
    commits.sort(key=lambda x: x["committed_at"])
    files.sort(key=lambda x: (x["sha"], x["path"]))
    return commits, files


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def flatten_pr(pr: dict[str, Any]) -> dict[str, Any]:
    reviews = pr.get("reviews") or {}
    labels = [n["name"] for n in (pr.get("labels") or {}).get("nodes", [])]
    author = pr.get("author") or {}
    merged_by = pr.get("mergedBy") or {}
    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "url": pr.get("url"),
        "author_login": author.get("login"),
        "author_name": None,
        "author_company": None,
        "merged_by_login": merged_by.get("login"),
        "created_at": pr.get("createdAt"),
        "merged_at": pr.get("mergedAt"),
        "updated_at": pr.get("updatedAt"),
        "base_ref": pr.get("baseRefName"),
        "head_ref": pr.get("headRefName"),
        "is_cross_repository": pr.get("isCrossRepository"),
        "additions": pr.get("additions"),
        "deletions": pr.get("deletions"),
        "changed_files": pr.get("changedFiles"),
        "commit_count": (pr.get("commits") or {}).get("totalCount"),
        "conversation_comment_count": (pr.get("comments") or {}).get("totalCount"),
        "review_count": reviews.get("totalCount"),
        "review_thread_count": (pr.get("reviewThreads") or {}).get("totalCount"),
        "participant_count": None,
        "closing_issue_count": (pr.get("closingIssuesReferences") or {}).get("totalCount"),
        "labels": "|".join(labels),
        "participants_sample": "",
        "closing_issues": "",
        "body_chars": None,
    }


def build_pr_git_stats(
    commits: list[dict[str, Any]],
    files: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    stats: dict[int, dict[str, Any]] = defaultdict(
        lambda: {
            "git_commit_count": 0,
            "git_file_count": 0,
            "git_paths": set(),
            "git_categories": Counter(),
        }
    )
    for commit in commits:
        pr_number = commit.get("pr_number")
        if not pr_number:
            continue
        stats[int(pr_number)]["git_commit_count"] += 1
    for file_row in files:
        pr_number = file_row.get("pr_number")
        if not pr_number:
            continue
        row = stats[int(pr_number)]
        row["git_file_count"] += 1
        row["git_paths"].add(file_row.get("path"))
        row["git_categories"][file_row.get("category") or "unknown"] += 1
    normalized: dict[int, dict[str, Any]] = {}
    for pr_number, row in stats.items():
        categories = row["git_categories"]
        normalized[pr_number] = {
            "git_commit_count": row["git_commit_count"],
            "git_file_count": row["git_file_count"],
            "git_unique_file_count": len(row["git_paths"]),
            "git_category_count": len(categories),
            "git_categories": "|".join(f"{k}:{v}" for k, v in sorted(categories.items())),
            "git_frontend_files": categories.get("frontend", 0),
            "git_product_backend_files": categories.get("product_backend", 0),
            "git_database_migration_files": categories.get("database_migration", 0),
            "git_infra_ci_files": categories.get("infra_ci", 0),
            "git_docs_files": categories.get("docs", 0),
            "git_tests_files": categories.get("tests", 0),
            "git_other_files": categories.get("other", 0),
        }
    return normalized


def build_pr_enriched_rows(
    prs: list[dict[str, Any]],
    commits: list[dict[str, Any]],
    files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    stats_by_pr = build_pr_git_stats(commits, files)
    rows: list[dict[str, Any]] = []
    for pr in prs:
        row = flatten_pr(pr)
        stats = stats_by_pr.get(int(row["number"]), {})
        row.update(
            {
                "git_commit_count": stats.get("git_commit_count", 0),
                "git_file_count": stats.get("git_file_count", 0),
                "git_unique_file_count": stats.get("git_unique_file_count", 0),
                "git_category_count": stats.get("git_category_count", 0),
                "git_categories": stats.get("git_categories", ""),
                "git_frontend_files": stats.get("git_frontend_files", 0),
                "git_product_backend_files": stats.get("git_product_backend_files", 0),
                "git_database_migration_files": stats.get("git_database_migration_files", 0),
                "git_infra_ci_files": stats.get("git_infra_ci_files", 0),
                "git_docs_files": stats.get("git_docs_files", 0),
                "git_tests_files": stats.get("git_tests_files", 0),
                "git_other_files": stats.get("git_other_files", 0),
                "is_revert_title": str(row.get("title") or "").lower().startswith("revert"),
            }
        )
        rows.append(row)
    return rows


def build_engineer_rollup(
    prs: list[dict[str, Any]],
    commits: list[dict[str, Any]],
    files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    stats_by_pr = build_pr_git_stats(commits, files)
    by_engineer: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "engineer": "",
            "merged_prs_authored": 0,
            "authored_pr_git_file_count": 0,
            "authored_pr_git_unique_file_count": 0,
            "authored_pr_git_category_touch_count": 0,
            "authored_pr_reverts": 0,
            "authored_pr_docs_files": 0,
            "authored_pr_tests_files": 0,
            "authored_pr_infra_ci_files": 0,
            "authored_pr_database_migration_files": 0,
            "authored_pr_reviews_received": 0,
            "authored_pr_closing_issues": 0,
            "reviews_submitted_sampled": 0,
            "approved_reviews_sampled": 0,
            "change_request_reviews_sampled": 0,
            "main_branch_commits": 0,
            "main_branch_files_touched": 0,
            "main_branch_categories_touched": set(),
            "main_branch_prs_linked": set(),
        }
    )
    for pr in prs:
        author = ((pr.get("author") or {}).get("login")) or "unknown"
        row = by_engineer[author]
        row["engineer"] = author
        row["merged_prs_authored"] += 1
        pr_stats = stats_by_pr.get(int(pr["number"]), {})
        row["authored_pr_git_file_count"] += pr_stats.get("git_file_count", 0)
        row["authored_pr_git_unique_file_count"] += pr_stats.get("git_unique_file_count", 0)
        row["authored_pr_git_category_touch_count"] += pr_stats.get("git_category_count", 0)
        row["authored_pr_docs_files"] += pr_stats.get("git_docs_files", 0)
        row["authored_pr_tests_files"] += pr_stats.get("git_tests_files", 0)
        row["authored_pr_infra_ci_files"] += pr_stats.get("git_infra_ci_files", 0)
        row["authored_pr_database_migration_files"] += pr_stats.get("git_database_migration_files", 0)
        if str(pr.get("title") or "").lower().startswith("revert"):
            row["authored_pr_reverts"] += 1
        row["authored_pr_reviews_received"] += (pr.get("reviews") or {}).get("totalCount") or 0
        row["authored_pr_closing_issues"] += (pr.get("closingIssuesReferences") or {}).get("totalCount") or 0
    commit_by_sha = {c["sha"]: c for c in commits}
    for commit in commits:
        # Use the local git author email/name for commits. Later joins can map these
        # to GitHub logins via PR author where a squash PR number is present.
        engineer = commit.get("author_email") or commit.get("author_name") or "unknown"
        row = by_engineer[engineer]
        row["engineer"] = engineer
        row["main_branch_commits"] += 1
        if commit.get("pr_number"):
            row["main_branch_prs_linked"].add(commit["pr_number"])
    for file_row in files:
        commit = commit_by_sha.get(file_row["sha"])
        if not commit:
            continue
        engineer = commit.get("author_email") or commit.get("author_name") or "unknown"
        row = by_engineer[engineer]
        row["engineer"] = engineer
        row["main_branch_files_touched"] += 1
        row["main_branch_categories_touched"].add(file_row.get("category") or "unknown")
    rows = []
    for row in by_engineer.values():
        normalized = dict(row)
        normalized["main_branch_categories_touched"] = "|".join(sorted(normalized["main_branch_categories_touched"]))
        normalized["main_branch_prs_linked"] = len(normalized["main_branch_prs_linked"])
        rows.append(normalized)
    rows.sort(key=lambda x: (x["merged_prs_authored"], x["authored_pr_git_unique_file_count"]), reverse=True)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--out", type=Path, default=Path("data"))
    parser.add_argument("--until", type=str, default=None, help="ISO timestamp or YYYY-MM-DD; defaults to now UTC")
    parser.add_argument("--skip-clone", action="store_true")
    parser.add_argument("--refresh-prs", action="store_true")
    parser.add_argument("--refresh-enrichment", action="store_true")
    args = parser.parse_args()

    until = utc_now() if args.until is None else dt.datetime.fromisoformat(args.until.replace("Z", "+00:00"))
    if until.tzinfo is None:
        until = until.replace(tzinfo=dt.timezone.utc)
    since = until - dt.timedelta(days=args.days)

    raw_dir = args.out / "raw"
    processed_dir = args.out / "processed"
    external_dir = args.out / "external" / "posthog"
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)

    token = get_gh_token()
    client = GitHubClient(token)

    print(f"Fetching {FULL_REPO} data from {isoformat_z(since)} to {isoformat_z(until)}", file=sys.stderr)
    repo_meta = client.rest(f"/repos/{FULL_REPO}")
    write_json(raw_dir / "repo.json", repo_meta)

    pr_path = raw_dir / "merged_prs_search.json"
    legacy_pr_path = raw_dir / "merged_prs_graphql.json"
    if pr_path.exists() and not args.refresh_prs:
        prs = json.loads(pr_path.read_text(encoding="utf-8"))
        print(f"Reusing existing PR search data from {pr_path}", file=sys.stderr)
    elif legacy_pr_path.exists() and not args.refresh_prs:
        prs = json.loads(legacy_pr_path.read_text(encoding="utf-8"))
        print(f"Reusing existing PR search data from {legacy_pr_path}", file=sys.stderr)
        write_json(pr_path, prs)
    else:
        prs = fetch_merged_prs_rest(client, since, until)
        write_json(pr_path, prs)

    closed_issues_path = raw_dir / "closed_issues_search.json"
    if closed_issues_path.exists() and not args.refresh_enrichment:
        closed_issues = json.loads(closed_issues_path.read_text(encoding="utf-8"))
    else:
        closed_issues = rest_search_issue_windows(
            client,
            f"repo:{FULL_REPO} is:issue",
            "closed",
            since,
            until,
        )
        write_json(closed_issues_path, closed_issues)

    updated_issues_path = raw_dir / "updated_issues_search.json"
    if updated_issues_path.exists() and not args.refresh_enrichment:
        updated_issues = json.loads(updated_issues_path.read_text(encoding="utf-8"))
    else:
        updated_issues = rest_search_issue_windows(
            client,
            f"repo:{FULL_REPO} is:issue",
            "updated",
            since,
            until,
        )
        write_json(updated_issues_path, updated_issues)

    releases_path = raw_dir / "recent_releases.json"
    if releases_path.exists() and not args.refresh_enrichment:
        releases = json.loads(releases_path.read_text(encoding="utf-8"))
    else:
        releases = client.rest_paginated(f"/repos/{FULL_REPO}/releases", {"per_page": 100}, max_pages=3)
        write_json(releases_path, releases)

    workflows_path = raw_dir / "actions_workflows.json"
    if workflows_path.exists() and not args.refresh_enrichment:
        workflows = json.loads(workflows_path.read_text(encoding="utf-8"))
    else:
        workflows = client.rest(f"/repos/{FULL_REPO}/actions/workflows", {"per_page": 100})
        write_json(workflows_path, workflows)

    workflow_count_path = raw_dir / "actions_runs_count_only.json"
    if workflow_count_path.exists() and not args.refresh_enrichment:
        workflow_run_count = json.loads(workflow_count_path.read_text(encoding="utf-8"))
    else:
        workflow_run_count = client.rest(
            f"/repos/{FULL_REPO}/actions/runs",
            {"created": f"{since.date().isoformat()}..{until.date().isoformat()}", "per_page": 1},
        )
        write_json(workflow_count_path, workflow_run_count)

    commits: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    if not args.skip_clone:
        ensure_posthog_git_clone(external_dir)
        commits, files = parse_git_log(external_dir, since, until)
        write_json(raw_dir / "git_commits_master_since.json", commits)
        write_csv(processed_dir / "git_commits_master_since.csv", commits)
        write_csv(processed_dir / "git_commit_files_master_since.csv", files)

    write_csv(processed_dir / "pr_flat.csv", [flatten_pr(pr) for pr in prs])
    write_csv(processed_dir / "pr_enriched.csv", build_pr_enriched_rows(prs, commits, files))
    engineer_rollup = build_engineer_rollup(prs, commits, files)
    write_csv(processed_dir / "engineer_rollup_initial.csv", engineer_rollup)

    manifest = {
        "repo": FULL_REPO,
        "window": {
            "since": isoformat_z(since),
            "until": isoformat_z(until),
            "days": args.days,
        },
        "generated_at": isoformat_z(utc_now()),
        "counts": {
            "merged_prs": len(prs),
            "closed_issues_total": sum(w["total_count"] for w in closed_issues["window_counts"]),
            "closed_issues_downloaded": closed_issues["downloaded_count"],
            "updated_issues_total": sum(w["total_count"] for w in updated_issues["window_counts"]),
            "updated_issues_downloaded": updated_issues["downloaded_count"],
            "recent_releases_downloaded": len(releases),
            "actions_workflows_downloaded": len(workflows.get("workflows", [])) if isinstance(workflows, dict) else len(workflows),
            "actions_runs_total_count_only": workflow_run_count.get("total_count"),
            "master_commits": len(commits),
            "master_commit_file_rows": len(files),
            "engineer_rollup_rows": len(engineer_rollup),
        },
        "caveats": [
            "Merged PR index data is complete for the window from date-bucketed GitHub REST Search. Review identities, review bodies, and review total counts are not included in the all-PR pass and should be fetched later for the top-candidate subset.",
            "Commit and per-file path changes are complete for the shallow master-branch history included in the clone depth, not for unmerged branches. Line-level numstat is intentionally skipped because it is slow at PostHog's 90-day scale and would bias the analysis toward LoC.",
            "Actions run count is recorded, but individual run records are not downloaded because the count is too large for a 90-minute assignment-scale dataset.",
            "GitHub-only data cannot directly measure product adoption, customer satisfaction, meetings, flow-state time, or production incidents unless linked external systems are added later.",
            "Full PR body text and linked issue node details are intentionally not downloaded in the all-PR pass to keep GitHub GraphQL responses reliable at PostHog's volume; they can be fetched later for the top-candidate subset.",
        ],
    }
    write_json(args.out / "manifest.json", manifest)
    print(json.dumps(manifest, indent=2), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
