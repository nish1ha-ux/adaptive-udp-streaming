"""
Build ``frontend/data/activity.json`` from the repository's git history.

The dashboard's credits page shows commit counts and recent commits per member.
Those numbers are recorded facts, so they are generated from ``git log`` rather than
typed by hand. Re-run after fetching new work::

    git fetch --all
    python scripts/build_data.py
"""

import json
import subprocess
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend" / "data" / "activity.json"
RECENT = 5

# Git author names that belong to each member (case-insensitive match).
MEMBERS: Dict[str, List[str]] = {
    "Nishtha Bhushan": ["nishtha bhushan", "nish1ha-ux"],
    "Prakhar Manish Srivastava": ["prakhar manish srivastava"],
    "Subham Kumar Sahu": ["subhamkumarsahu929", "subham kumar sahu"],
    "Sudhanshu Aryan": ["sudhanshu aryan"],
}


def git_log() -> List[Dict[str, str]]:
    """Return non-merge commits from every local and remote branch, newest first."""
    fmt = "%H%x1f%an%x1f%ad%x1f%s"
    out = subprocess.run(
        ["git", "log", "--all", "--no-merges", "--date=short", f"--format={fmt}"],
        cwd=ROOT, capture_output=True, text=True, check=True, encoding="utf-8",
    ).stdout
    rows = []
    for line in out.splitlines():
        sha, author, date, subject = line.split("\x1f")
        rows.append({"sha": sha[:7], "author": author, "date": date, "subject": subject})
    # The same commit can be reachable from several branches; git already de-duplicates by hash.
    return rows


def main() -> None:
    commits = git_log()
    result = {}
    for member, aliases in MEMBERS.items():
        mine = [c for c in commits if c["author"].lower() in aliases]
        result[member] = {
            "commits": len(mine),
            "first": mine[-1]["date"] if mine else None,
            "last": mine[0]["date"] if mine else None,
            "recent": [{k: c[k] for k in ("sha", "date", "subject")} for c in mine[:RECENT]],
        }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"total_commits": len(commits), "members": result}, indent=2), encoding="utf-8")
    print(f"Wrote {OUT} ({len(commits)} commits)")


if __name__ == "__main__":
    main()
