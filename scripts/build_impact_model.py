#!/usr/bin/env python3
"""Build the compact impact model consumed by the dashboard."""

from __future__ import annotations

import csv
import datetime as dt
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DATA = Path("data")
OUT = Path("src/data")
PUBLIC = Path("public")

CORE_CATEGORIES = ["frontend", "product_backend", "infra_ci", "other"]
BOT_HINTS = ("[bot]", "bot", "mendral-app", "posthog[bot]")


def parse_int(value: Any, default: int = 0) -> int:
    try:
        if value in ("", None):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def parse_date(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 1.0
    ordered = sorted(values)
    idx = (len(ordered) - 1) * pct
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - idx) + ordered[hi] * (idx - lo)


def percentile_rank_score(value: float, values: list[float]) -> float:
    """Mid-rank empirical percentile. The top value is high, but not a fake 100."""
    if not values:
        return 0.0
    less = sum(1 for item in values if item < value)
    equal = sum(1 for item in values if item == value)
    return round(100 * (less + 0.5 * equal) / len(values), 1)


def write_engineer_csv(path: Path, ranked: list[dict[str, Any]]) -> None:
    fields = [
        "rank",
        "login",
        "impactScore",
        "deliveryScore",
        "architectureScore",
        "qualityScore",
        "collaborationScore",
        "deliveryRaw",
        "architectureRaw",
        "qualityRaw",
        "collaborationRaw",
        "mergedPrs",
        "homeArea",
        "crossBoundaryPrs",
        "crossBoundaryRate",
        "reviewCount",
        "reviewThreadCount",
        "conversationComments",
        "medianCycleHours",
        "features",
        "fixes",
        "refactors",
        "docsPrs",
        "reverts",
        "testsTouched",
        "infraTouched",
        "uniqueFiles",
        "categoryTouches",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in ranked:
            writer.writerow(row)


def is_bot(login: str) -> bool:
    lower = (login or "").lower()
    return any(hint in lower for hint in BOT_HINTS)


def intent_for(title: str, labels: str) -> str:
    t = (title or "").strip().lower()
    label_set = {label.lower() for label in (labels or "").split("|") if label}
    if t.startswith("revert"):
        return "revert"
    if "dependencies" in label_set or "automated" in label_set:
        return "dependency"
    match = re.match(r"([a-z]+)(?:\(|:|\s)", t)
    prefix = match.group(1) if match else ""
    if prefix in {"feat", "fix", "perf", "refactor", "docs", "test", "ci", "chore"}:
        return prefix
    if "bug" in label_set:
        return "fix"
    if "infrastructure" in label_set:
        return "ci"
    return "other"


INTENT_WEIGHTS = {
    "perf": 1.25,
    "feat": 1.18,
    "fix": 1.16,
    "refactor": 1.08,
    "ci": 1.05,
    "test": 0.92,
    "revert": 0.80,
    "docs": 0.78,
    "chore": 0.72,
    "dependency": 0.45,
    "other": 0.85,
}


def quality_weight(intent: str, row: dict[str, Any]) -> float:
    value = 0.0
    if intent == "fix":
        value += 1.0
    if intent == "perf":
        value += 1.2
    if intent == "refactor":
        value += 0.7
    if intent == "revert":
        value += 0.8
    if intent in {"test", "ci"}:
        value += 0.5
    if parse_int(row.get("git_tests_files")) > 0:
        value += 0.4
    if parse_int(row.get("git_infra_ci_files")) > 0:
        value += 0.4
    if parse_int(row.get("git_docs_files")) > 0:
        value += 0.15
    return value


def category_counts(row: dict[str, Any]) -> Counter[str]:
    return Counter(
        {
            "frontend": parse_int(row.get("git_frontend_files")),
            "product_backend": parse_int(row.get("git_product_backend_files")),
            "infra_ci": parse_int(row.get("git_infra_ci_files")),
            "docs": parse_int(row.get("git_docs_files")),
            "tests": parse_int(row.get("git_tests_files")),
            "other": parse_int(row.get("git_other_files")),
        }
    )


def main() -> int:
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    prs = list(csv.DictReader((DATA / "processed/pr_enriched.csv").open(encoding="utf-8")))
    collaboration = {
        int(row["number"]): row
        for row in json.loads((DATA / "raw/pr_collaboration_totals.json").read_text(encoding="utf-8"))["rows"]
    }

    # First pass: infer each human engineer's dominant area. This is intentionally
    # a proxy, not a team ownership map.
    home_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in prs:
        author = row.get("author_login") or "unknown"
        if is_bot(author):
            continue
        cats = category_counts(row)
        for category in CORE_CATEGORIES:
            home_counts[author][category] += cats[category]
    home_area = {}
    for author, counts in home_counts.items():
        if not counts or counts.total() == 0:
            home_area[author] = "unknown"
        else:
            home_area[author] = counts.most_common(1)[0][0]

    engineer: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "login": "",
            "homeArea": "unknown",
            "mergedPrs": 0,
            "deliveryRaw": 0.0,
            "architectureRaw": 0.0,
            "qualityRaw": 0.0,
            "collaborationRaw": 0.0,
            "reviewCount": 0,
            "reviewThreadCount": 0,
            "conversationComments": 0,
            "crossBoundaryPrs": 0,
            "reverts": 0,
            "features": 0,
            "fixes": 0,
            "refactors": 0,
            "docsPrs": 0,
            "testsTouched": 0,
            "infraTouched": 0,
            "uniqueFiles": 0,
            "categoryTouches": 0,
            "cycleHours": [],
            "topPrs": [],
            "intentCounts": Counter(),
        }
    )
    pr_rows: list[dict[str, Any]] = []
    excluded_bots = Counter()

    for row in prs:
        author = row.get("author_login") or "unknown"
        if is_bot(author):
            excluded_bots[author] += 1
            continue
        number = parse_int(row["number"])
        collab = collaboration.get(number, {})
        intent = intent_for(row.get("title") or "", row.get("labels") or "")
        files = parse_int(row.get("git_unique_file_count")) or 1
        categories_touched = parse_int(row.get("git_category_count"))
        cats = category_counts(row)
        area = home_area.get(author, "unknown")
        non_home_core = sum(v for k, v in cats.items() if k in CORE_CATEGORIES and k != area)
        cross_boundary = bool(area != "unknown" and non_home_core > 0)
        stewardship = (
            parse_int(row.get("git_tests_files")) > 0
            or parse_int(row.get("git_docs_files")) > 0
            or parse_int(row.get("git_infra_ci_files")) > 0
        )

        scope = math.log1p(files)
        delivery = INTENT_WEIGHTS.get(intent, INTENT_WEIGHTS["other"]) * scope
        architecture = scope * (
            (0.65 if cross_boundary else 0.0)
            + 0.25 * min(max(categories_touched - 1, 0), 3)
            + (0.20 if stewardship else 0.0)
        )
        quality = scope * quality_weight(intent, row)
        review_count = parse_int(collab.get("review_count"))
        thread_count = parse_int(collab.get("review_thread_count"))
        comment_count = parse_int(collab.get("conversation_comments"), parse_int(row.get("conversation_comment_count")))
        collaboration_score = math.log1p(review_count + thread_count + 0.5 * comment_count) * math.sqrt(scope)

        created = parse_date(row["created_at"]) if row.get("created_at") else None
        merged = parse_date(row["merged_at"]) if row.get("merged_at") else None
        cycle_hours = ((merged - created).total_seconds() / 3600) if created and merged else None

        e = engineer[author]
        e["login"] = author
        e["homeArea"] = area
        e["mergedPrs"] += 1
        e["deliveryRaw"] += delivery
        e["architectureRaw"] += architecture
        e["qualityRaw"] += quality
        e["collaborationRaw"] += collaboration_score
        e["reviewCount"] += review_count
        e["reviewThreadCount"] += thread_count
        e["conversationComments"] += comment_count
        e["uniqueFiles"] += files
        e["categoryTouches"] += categories_touched
        e["intentCounts"][intent] += 1
        if cross_boundary:
            e["crossBoundaryPrs"] += 1
        if intent == "revert":
            e["reverts"] += 1
        if intent == "feat":
            e["features"] += 1
        if intent == "fix":
            e["fixes"] += 1
        if intent == "refactor":
            e["refactors"] += 1
        if intent == "docs":
            e["docsPrs"] += 1
        if parse_int(row.get("git_tests_files")) > 0:
            e["testsTouched"] += 1
        if parse_int(row.get("git_infra_ci_files")) > 0:
            e["infraTouched"] += 1
        if cycle_hours is not None:
            e["cycleHours"].append(cycle_hours)

        pr_score = delivery + architecture + quality + collaboration_score
        pr_summary = {
            "number": number,
            "title": row.get("title"),
            "url": row.get("url"),
            "intent": intent,
            "files": files,
            "reviews": review_count,
            "threads": thread_count,
            "score": round(pr_score, 2),
            "crossBoundary": cross_boundary,
        }
        e["topPrs"].append(pr_summary)
        pr_rows.append({**pr_summary, "author": author})

    humans = list(engineer.values())
    raw_values = {
        "delivery": [e["deliveryRaw"] for e in humans],
        "architecture": [e["architectureRaw"] for e in humans],
        "quality": [e["qualityRaw"] for e in humans],
        "collaboration": [e["collaborationRaw"] for e in humans],
    }
    baselines = {
        key: {
            "p50": percentile(values, 0.50),
            "p90": percentile(values, 0.90),
            "p99": percentile(values, 0.99),
            "max": max(values) if values else 0,
        }
        for key, values in raw_values.items()
    }

    for e in humans:
        e["deliveryScore"] = percentile_rank_score(e["deliveryRaw"], raw_values["delivery"])
        e["architectureScore"] = percentile_rank_score(e["architectureRaw"], raw_values["architecture"])
        e["qualityScore"] = percentile_rank_score(e["qualityRaw"], raw_values["quality"])
        e["collaborationScore"] = percentile_rank_score(e["collaborationRaw"], raw_values["collaboration"])
        e["impactScore"] = round(
            0.35 * e["deliveryScore"]
            + 0.25 * e["architectureScore"]
            + 0.25 * e["qualityScore"]
            + 0.15 * e["collaborationScore"],
            1,
        )
        cycles = sorted(e["cycleHours"])
        e["medianCycleHours"] = round(cycles[len(cycles) // 2], 1) if cycles else None
        e["topPrs"] = sorted(e["topPrs"], key=lambda x: x["score"], reverse=True)[:4]
        e["intentCounts"] = dict(e["intentCounts"].most_common())
        e["crossBoundaryRate"] = round(e["crossBoundaryPrs"] / e["mergedPrs"] * 100, 1) if e["mergedPrs"] else 0

    ranked = sorted(humans, key=lambda x: x["impactScore"], reverse=True)
    for idx, e in enumerate(ranked, start=1):
        e["rank"] = idx

    intent_counts = Counter()
    weekly = Counter()
    for row in prs:
        intent_counts[intent_for(row.get("title") or "", row.get("labels") or "")] += 1
        merged = parse_date(row["merged_at"])
        year, week, _ = merged.isocalendar()
        weekly[f"{year}-W{week:02d}"] += 1

    payload = {
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": {
            "repo": "PostHog/posthog",
            "window": manifest["window"],
            "counts": manifest["counts"],
            "collaborationTotals": len(collaboration),
            "humanEngineers": len(humans),
            "excludedBotPrs": sum(excluded_bots.values()),
            "excludedBotAccounts": dict(excluded_bots.most_common(10)),
            "gitJoinCoveragePct": 98.15,
        },
        "baselines": {
            key: {inner_key: round(inner_value, 3) for inner_key, inner_value in summary.items()}
            for key, summary in baselines.items()
        },
        "weights": {
            "delivery": 0.35,
            "architecture": 0.25,
            "quality": 0.25,
            "collaboration": 0.15,
        },
        "formula": {
            "scope": "S_p = ln(1 + max(F_p, 1))",
            "deliveryRaw": "D_e = sum_p W_intent(p) * S_p",
            "architectureRaw": "A_e = sum_p S_p * (0.65*X_p + 0.25*min(K_p-1,3) + 0.20*G_p)",
            "qualityRaw": "Q_e = sum_p S_p * Q_intent_and_stewardship(p)",
            "collaborationRaw": "C_e = sum_p ln(1 + R_p + T_p + 0.5*M_p) * sqrt(S_p)",
            "normalize": "N(x) = 100 * (count(raw < x) + 0.5*count(raw = x)) / n_humans",
            "impactScore": "EIS_e = 0.35*N(D_e) + 0.25*N(A_e) + 0.25*N(Q_e) + 0.15*N(C_e)",
        },
        "intentWeights": INTENT_WEIGHTS,
        "rankedEngineers": ranked[:50],
        "topFive": ranked[:5],
        "intentDistribution": dict(intent_counts.most_common()),
        "weeklyMergedPrs": [{"week": week, "count": count} for week, count in sorted(weekly.items())],
        "topPrs": sorted(pr_rows, key=lambda x: x["score"], reverse=True)[:80],
        "notes": {
            "pickedProblem": "Find engineers whose merged work combines product value, quality stewardship, cross-boundary design work, and review-discussion load in a high-autonomy repo.",
            "tradeoffs": [
                "Cross-boundary work is inferred from touched path categories and each engineer's dominant area, not a true ownership map.",
                "Per-PR scope uses log(file paths), not lines of code, to reduce incentives for giant changes.",
                "Component scores use empirical mid-rank percentiles, so a dominant engineer can rank first without receiving a misleading perfect 100.",
                "Review totals measure discussion carried by authored PRs; reviewer mentoring credit would require per-review author extraction.",
            ],
            "leftOut": [
                "LoC and raw commit count as primary score inputs.",
                "Perceptual SPACE data such as satisfaction, flow time, and meeting load.",
                "Production incident/customer adoption impact because it is outside GitHub.",
                "Full AI-generated-code attribution; labels like codex are visible but not scored.",
            ],
            "breaksFirst": [
                "The home-area proxy breaks when someone intentionally rotates areas or owns cross-cutting platform work.",
                "Review-thread load can reward controversial PRs unless paired with review quality analysis.",
                "GitHub Search date buckets need splitting if weekly PR volume exceeds 1,000 results.",
                "Bot and automation classification needs maintenance as new automation accounts appear.",
            ],
            "next": [
                "Fetch review authors and comment bodies for top candidate PRs to score mentorship and review quality.",
                "Add CODEOWNERS or team ownership maps to replace the path-based cross-boundary proxy.",
                "Join incidents, releases, customer-facing changelogs, and PostHog product analytics to validate business impact.",
                "Detect AI-assisted PRs and separate human design contribution from generated mechanical changes.",
            ],
        },
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "impactData.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    write_engineer_csv(DATA / "processed/impact_engineers.csv", ranked)
    write_engineer_csv(PUBLIC / "impact_engineers.csv", ranked)
    print(json.dumps({"topFive": [(e["login"], e["impactScore"]) for e in ranked[:5]], "humans": len(humans)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
