#!/usr/bin/env python3
"""Refresh the live numbers inside dark_mode.svg and light_mode.svg.

Uses GitHub's GraphQL API with the token the Actions runner already provides
(GITHUB_TOKEN), so no personal access token or extra secret is needed.
Only public data is counted. Standard library only.

Updated SVG element ids:
  age_data, repo_data, star_data, commit_data, follower_data
(plus the matching *_dots elements, which keep the dotted leaders aligned).
"""
import calendar
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

LINE_W = 62  # characters per info line; must match the SVG layout
SVG_FILES = ["dark_mode.svg", "light_mode.svg"]

# element id prefix -> key label shown in the card (needed to size the dots)
FIELDS = {
    "age": "Uptime",
    "repo": "Repos",
    "star": "Stars",
    "commit": "Commits",
    "follower": "Followers",
}

API = "https://api.github.com/graphql"


def gql(query, variables, token):
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-card-updater",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.load(resp)
    if "errors" in body:
        raise RuntimeError(f"GraphQL errors: {body['errors']}")
    return body["data"]


def parse_time(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def plural(n, unit):
    return f"{n} {unit}{'' if n == 1 else 's'}"


def age_text(created, now):
    """'N years, M months, D days' between two datetimes."""
    years = now.year - created.year
    months = now.month - created.month
    days = now.day - created.day
    if days < 0:
        months -= 1
        prev_year, prev_month = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
        days += calendar.monthrange(prev_year, prev_month)[1]
    if months < 0:
        years -= 1
        months += 12
    return f"{plural(years, 'year')}, {plural(months, 'month')}, {plural(days, 'day')}"


def fetch_stats(login, token, now=None):
    now = now or datetime.now(timezone.utc)

    first = gql(
        """
        query($login: String!) {
          user(login: $login) {
            createdAt
            followers { totalCount }
            repositories(ownerAffiliations: OWNER, privacy: PUBLIC) { totalCount }
          }
        }""",
        {"login": login},
        token,
    )["user"]
    created = parse_time(first["createdAt"])

    # stars: page through public repos the user owns
    stars, cursor = 0, None
    while True:
        page = gql(
            """
            query($login: String!, $cursor: String) {
              user(login: $login) {
                repositories(first: 100, after: $cursor, ownerAffiliations: OWNER, privacy: PUBLIC) {
                  nodes { stargazerCount }
                  pageInfo { hasNextPage endCursor }
                }
              }
            }""",
            {"login": login, "cursor": cursor},
            token,
        )["user"]["repositories"]
        stars += sum(n["stargazerCount"] for n in page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    # commits: contributionsCollection covers at most one year per window
    parts = []
    for i, year in enumerate(range(created.year, now.year + 1)):
        start = max(created, datetime(year, 1, 1, tzinfo=timezone.utc))
        end = min(now, datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc))
        parts.append(
            f'y{i}: contributionsCollection(from: "{start:%Y-%m-%dT%H:%M:%SZ}", '
            f'to: "{end:%Y-%m-%dT%H:%M:%SZ}") {{ totalCommitContributions }}'
        )
    commits_data = gql(
        "query($login: String!) { user(login: $login) { " + " ".join(parts) + " } }",
        {"login": login},
        token,
    )["user"]
    commits = sum(v["totalCommitContributions"] for v in commits_data.values())

    return {
        "age": age_text(created, now),
        "repo": f"{first['repositories']['totalCount']:,}",
        "star": f"{stars:,}",
        "commit": f"{commits:,}",
        "follower": f"{first['followers']['totalCount']:,}",
    }


def dots_for(key, value):
    prefix = 2 + len(key) + 1  # ". " + key + ":"
    return " " + "." * max(2, LINE_W - prefix - 2 - len(value)) + " "


def set_tspan(svg, element_id, text):
    pattern = re.compile(r'(<tspan[^>]*\bid="%s"[^>]*>)([^<]*)(</tspan>)' % re.escape(element_id))
    new, n = pattern.subn(lambda m: m.group(1) + text + m.group(3), svg)
    if n != 1:
        raise RuntimeError(f"expected exactly one element with id={element_id}, found {n}")
    return new


def apply_stats(svg, stats):
    for fid, key in FIELDS.items():
        value = stats[fid]
        svg = set_tspan(svg, f"{fid}_data_dots", dots_for(key, value))
        svg = set_tspan(svg, f"{fid}_data", value)
    return svg


def main():
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    login = os.environ.get("USER_NAME") or os.environ.get("GITHUB_REPOSITORY_OWNER")
    if not token or not login:
        sys.exit("GITHUB_TOKEN and USER_NAME (or GITHUB_REPOSITORY_OWNER) must be set")

    stats = fetch_stats(login, token)
    print("Fetched:", stats)

    changed = False
    for name in SVG_FILES:
        with open(name, encoding="utf-8") as f:
            old = f.read()
        new = apply_stats(old, stats)
        if new != old:
            with open(name, "w", encoding="utf-8") as f:
                f.write(new)
            changed = True
    print("Updated SVGs" if changed else "No changes")


if __name__ == "__main__":
    main()
