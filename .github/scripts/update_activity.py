"""Refresh the profile's activity card from GitHub's contribution calendar.

Uses only the standard library and the repository-scoped GITHUB_TOKEN.
Never requests repository names, commit content, or private repository access.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from html import escape
import json
import os
from pathlib import Path
import re
import urllib.request

QUERY = """
query ProfileActivity($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def fetch_calendar(login: str, now: datetime) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9-]{1,39}", login):
        raise ValueError("Invalid GitHub username")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("The workflow's built-in GitHub token is required")
    try:
        first = now.date().replace(year=now.year - 1)
    except ValueError:  # February 29
        first = now.date().replace(year=now.year - 1, day=28)
    variables = {
        "login": login,
        "from": f"{first.isoformat()}T00:00:00Z",
        "to": now.isoformat().replace("+00:00", "Z"),
    }
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": variables}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "github-profile-activity-updater",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.load(response)
    if payload.get("errors"):
        raise RuntimeError("GitHub rejected the activity query: " + "; ".join(
            e.get("message", "GraphQL error") for e in payload["errors"]
        ))
    user = payload.get("data", {}).get("user")
    if not user:
        raise RuntimeError("GitHub user was not found")
    calendar = user["contributionsCollection"]["contributionCalendar"]
    days = [
        {"date": day["date"], "count": day["contributionCount"]}
        for week in calendar["weeks"] for day in week["contributionDays"]
        if first.isoformat() <= day["date"] <= now.date().isoformat()
    ]
    if not days or sum(d["count"] for d in days) != calendar["totalContributions"]:
        raise RuntimeError("Incomplete or inconsistent calendar; keeping the last good card")
    return {"total": calendar["totalContributions"], "days": days}


def calculate(calendar: dict, today: date) -> dict:
    by_date = {}
    for item in calendar["days"]:
        day = date.fromisoformat(item["date"])
        count = item["count"]
        if not isinstance(count, int) or count < 0:
            raise ValueError("Contribution counts must be non-negative integers")
        if day <= today:
            by_date[day] = count
    if not by_date:
        raise ValueError("An empty calendar cannot replace the last good card")
    first = min(by_date)
    days = []
    cursor = first
    longest = run = 0
    months = defaultdict(int)
    while cursor <= today:
        count = by_date.get(cursor, 0)
        run = run + 1 if count else 0
        longest = max(longest, run)
        months[cursor.strftime("%Y-%m")] += count
        days.append({"date": cursor.isoformat(), "count": count})
        cursor += timedelta(days=1)
    # Today can remain empty until the user contributes; yesterday's streak stays active.
    cursor = today if by_date.get(today, 0) else today - timedelta(days=1)
    current = 0
    while cursor >= first and by_date.get(cursor, 0):
        current += 1
        cursor -= timedelta(days=1)
    total = sum(item["count"] for item in days)
    if total != calendar["total"]:
        raise ValueError("Calendar total does not match daily counts")
    return {"total": total, "current": current, "longest": longest,
            "first": first.isoformat(), "last": today.isoformat(),
            "months": dict(sorted(months.items())), "days": days}


def render_svg(stats: dict, now: datetime) -> str:
    updated = now.strftime("%d %b %Y %H:%M UTC")
    p = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="550" viewBox="0 0 1200 550" role="img" aria-labelledby="title desc">',
        '<title id="title">Automatically updated GitHub activity</title>',
        f'<desc id="desc">Updated {escape(updated)}. {stats["total"]} contributions, current streak {stats["current"]} days, longest streak {stats["longest"]} days within the displayed year.</desc>',
        '<rect width="1200" height="550" rx="20" fill="#0B1220"/>',
        '<g font-family="Segoe UI,Arial,sans-serif">',
        '<text x="40" y="45" font-size="16" letter-spacing="3" fill="#57E3CC">GITHUB ACTIVITY / AUTO UPDATED</text>',
        f'<text x="1160" y="45" text-anchor="end" font-size="15" fill="#A7BED1">{updated}</text>',
    ]
    cards = [(stats["total"], "Contributions", "Displayed year", "#64ADFF"),
             (stats["current"], "Current streak", "Consecutive active days", "#57E3CC"),
             (stats["longest"], "Longest streak", "Within displayed year", "#B99AFF")]
    for i, (count, label, caption, color) in enumerate(cards):
        x = 40 + i * 385
        p.append(f'<rect x="{x}" y="77" width="350" height="172" rx="14" fill="#132235" stroke="#294057"/>')
        p.append(f'<text x="{x+26}" y="146" font-size="49" font-weight="700" fill="{color}">{count:,}</text>')
        p.append(f'<text x="{x+26}" y="186" font-size="21" font-weight="600" fill="#F0F6FC">{label}</text>')
        p.append(f'<text x="{x+26}" y="218" font-size="16" fill="#A7BED1">{caption}</text>')
    p.append('<text x="40" y="291" font-size="18" font-weight="600" fill="#F0F6FC">Monthly contributions</text>')
    maximum = max(1, max(stats["months"].values()))
    width = 1120 / len(stats["months"])
    for i, (month, count) in enumerate(stats["months"].items()):
        x = 40 + i * width + 12
        height = round(count / maximum * 110)
        bar_width = width - 28
        label = datetime.strptime(month, "%Y-%m").strftime("%b")
        p.append(f'<rect x="{x:.1f}" y="{437-height}" width="{bar_width:.1f}" height="{height}" rx="5" fill="#57E3CC" opacity="{0.45+0.5*count/maximum:.3f}"/>')
        p.append(f'<text x="{x+bar_width/2:.1f}" y="{427-height}" text-anchor="middle" fill="#A7BED1" font-size="14">{count}</text>')
        p.append(f'<text x="{x+bar_width/2:.1f}" y="460" text-anchor="middle" fill="#A7BED1" font-size="14">{label}</text>')
    p += [
        f'<text x="40" y="496" fill="#A7BED1" font-size="15">Period: {stats["first"]} to {stats["last"]}. First and last months are partial.</text>',
        '<text x="40" y="525" fill="#829BAF" font-size="14">Source: GitHub contribution calendar. Today may be empty while yesterday\'s streak remains active.</text>',
        '</g></svg>',
    ]
    return "\n".join(p) + "\n"


def refresh_readme(text: str) -> str:
    # Keep all prose, galleries, links, and portfolio charts outside this card intact.
    text, image_count = re.subn(
        r'(<img\b[^>]*src="\./assets/github-activity\.svg(?:\?[^\"]*)?"[^>]*alt=")[^\"]*(")',
        r'\1GitHub activity updated daily from actual account data: contributions, current streak, longest streak, and monthly chart.\2',
        text,
    )
    if image_count != 1:
        raise ValueError("Expected exactly one activity card in README")
    note = ('<sub>Updated daily by [GitHub Actions](https://github.com/moodyfuad/moodyfuad/actions/workflows/update-profile.yml) '
            'from GitHub\'s contribution calendar. The card shows its latest successful update time. '
            'Streaks cover the displayed year; today may be empty while yesterday\'s streak remains active.</sub>')
    text, notes = re.subn(r'<sub>(?:Snapshot:|Updated daily by).*?</sub>', lambda _: note, text, count=1, flags=re.S)
    if notes != 1:
        raise ValueError("Expected exactly one activity update note in README")
    return text


def self_test() -> None:
    sample = {"total": 8, "days": [
        {"date": "2026-11-08", "count": 1},
        {"date": "2026-11-09", "count": 0},
        {"date": "2026-11-10", "count": 2},
        {"date": "2026-11-11", "count": 5},
    ]}
    s = calculate(sample, date(2026, 11, 11))
    assert (s["total"], s["current"], s["longest"]) == (8, 2, 2)
    assert calculate(sample, date(2026, 11, 12))["current"] == 2
    assert calculate(sample, date(2026, 11, 13))["current"] == 0
    assert calculate({"total": 2, "days": [{"date": "2026-11-08", "count": 1}, {"date": "2026-11-10", "count": 1}]}, date(2026, 11, 10))["longest"] == 1
    zero = calculate({"total": 0, "days": [{"date": "2026-11-11", "count": 0}]}, date(2026, 11, 11))
    assert zero["current"] == zero["longest"] == 0
    assert '<svg' in render_svg(zero, datetime(2026, 11, 11, tzinfo=timezone.utc))
    try:
        calculate({"total": 99, "days": sample["days"]}, date(2026, 11, 11))
    except ValueError:
        pass
    else:
        raise AssertionError("Inconsistent totals must fail")
    print("Activity calculation checks passed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    now = datetime.now(timezone.utc).replace(microsecond=0)
    if args.fixture:
        calendar = json.loads(args.fixture.read_text(encoding="utf-8"))
        now = datetime.combine(date.fromisoformat(max(d["date"] for d in calendar["days"])), datetime.min.time(), timezone.utc)
    else:
        calendar = fetch_calendar(os.environ["GITHUB_REPOSITORY_OWNER"], now)
    stats = calculate(calendar, now.date())
    svg = render_svg(stats, now)
    readme_path = args.root / "README.md"
    updated_readme = refresh_readme(readme_path.read_text(encoding="utf-8"))
    # Compute and validate everything before touching the last known good output.
    assets = args.root / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "github-activity.svg").write_text(svg, encoding="utf-8")
    readme_path.write_text(updated_readme, encoding="utf-8")
    print(f"Updated: {stats['total']} contributions; streak {stats['current']}; longest {stats['longest']}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write(f"## Profile activity refreshed\n\nUpdated {now.isoformat()}.\n\n| Contributions | Current streak | Longest streak |\n|---|---|---|\n| {stats['total']} | {stats['current']} days | {stats['longest']} days |\n")


if __name__ == "__main__":
    main()
