#!/usr/bin/env python3
"""
board.py — render and seed Graunt's wanted/offered board from board/wanted.json.

  render   Write board/WANTED.md (index with one-click "open as issue" links)
           and board/wanted/<slug>.md (one spec per file).
  seed     Create the labels and one GitHub issue per spec using the `gh` CLI
           (requires `gh auth login` with push access to the repo). Skips specs
           whose title already exists as an open or closed issue. Idempotent.
  urls     Print the prefilled new-issue URL for every spec (no gh required).

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "board"
REPO = "Grauntketplace/graunt-plugin"


def load() -> dict:
    return json.loads((BOARD / "wanted.json").read_text(encoding="utf-8"))


def issue_title(spec: dict) -> str:
    return f"Wanted: {spec['title']}"


def issue_body(spec: dict) -> str:
    return (
        f"**What**\n{spec['what']}\n\n"
        f"**Task it serves**\n{spec['task']}\n\n"
        f"**Coverage and freshness**\n{spec['coverage_freshness']}\n\n"
        f"**Rights a buyer needs**\n{spec['rights_needed']}\n\n"
        f"**Format**\n{spec['format']}\n\n"
        f"**Where a seller could start**\n{spec['sources_hint']}\n\n"
        f"**Why this is on the board**\n{spec['why']}\n\n"
        "---\n"
        "Seeded by Graunt from what agents ask this plugin for; not yet requested by a named buyer. "
        "A 👍 or a comment saying you need it turns it into real demand.\n\n"
        "**To claim it:** comment `claiming`. **To list it:** `/prepare-packet <path>` in Claude Code with the "
        "[Graunt plugin](https://github.com/Grauntketplace/graunt-plugin), or upload at https://graunt.com/prepare. "
        "Listing is free; the first listing that meets this spec is featured in the plugin's `/find-data` results and this repository's README.\n"
    )


def new_issue_url(spec: dict) -> str:
    q = {
        "title": issue_title(spec),
        "body": issue_body(spec),
        "labels": f"wanted-packet,seeded,{spec['category']}",
    }
    return f"https://github.com/{REPO}/issues/new?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)


def cmd_render(_: argparse.Namespace) -> int:
    data = load()
    (BOARD / "wanted").mkdir(parents=True, exist_ok=True)
    rows = []
    for spec in data["wanted"]:
        path = BOARD / "wanted" / f"{spec['slug']}.md"
        path.write_text(f"# {issue_title(spec)}\n\n_Category: `{spec['category']}`_\n\n{issue_body(spec)}", encoding="utf-8")
        rows.append(
            f"| [{spec['title']}](wanted/{spec['slug']}.md) | `{spec['category']}` | {spec['summary']} | "
            f"[open as issue]({new_issue_url(spec)}) |"
        )
    index = (
        "# Wanted packets\n\n"
        "Data that agents using this plugin ask for and the Graunt catalog does not yet have. "
        "Each row is a spec a seller can build to. Listing is free; the first listing that meets a spec is featured in "
        "`/find-data` results and in the README.\n\n"
        "- **Have it?** Open the spec, comment `claiming` on its issue (or open the issue via the link), then list it with "
        "`/prepare-packet <path>` or at https://graunt.com/prepare.\n"
        "- **Need it too?** 👍 the issue or comment with your use case; that is how sellers decide what to build first.\n"
        "- **Need something else?** Open a [wanted-packet issue](https://github.com/Grauntketplace/graunt-plugin/issues/new?template=wanted-packet.yml).\n\n"
        f"Live board: https://github.com/{REPO}/issues?q=is%3Aissue+is%3Aopen+label%3Awanted-packet\n\n"
        "| Packet | Category | Summary | |\n|---|---|---|---|\n" + "\n".join(rows) + "\n\n"
        "_Source of truth: `board/wanted.json`. Regenerate with `python3 scripts/board.py render`._\n"
    )
    (BOARD / "WANTED.md").write_text(index, encoding="utf-8")
    print(f"rendered {len(rows)} specs -> board/WANTED.md and board/wanted/*.md")
    return 0


def cmd_urls(_: argparse.Namespace) -> int:
    for spec in load()["wanted"]:
        print(new_issue_url(spec))
        print()
    return 0


def gh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], text=True, capture_output=True, check=check)


def cmd_seed(args: argparse.Namespace) -> int:
    if not shutil.which("gh"):
        print("gh CLI not found. Install it (https://cli.github.com) and run `gh auth login`, or use `urls` and open each link.", file=sys.stderr)
        return 2
    data = load()
    repo = args.repo
    for name, (color, desc) in data["labels"].items():
        r = gh("label", "create", name, "--repo", repo, "--color", color, "--description", desc, "--force", check=False)
        print(("label ok   " if r.returncode == 0 else "label FAIL ") + name + ("" if r.returncode == 0 else ": " + r.stderr.strip()))
    existing = set()
    r = gh("issue", "list", "--repo", repo, "--state", "all", "--limit", "500", "--json", "title", check=False)
    if r.returncode == 0:
        existing = {i["title"] for i in json.loads(r.stdout)}
    created = skipped = failed = 0
    for spec in data["wanted"]:
        title = issue_title(spec)
        if title in existing:
            skipped += 1
            print("skip (exists) " + title)
            continue
        r = gh("issue", "create", "--repo", repo, "--title", title, "--body", issue_body(spec),
               "--label", f"wanted-packet,seeded,{spec['category']}", check=False)
        if r.returncode == 0:
            created += 1
            print("created " + r.stdout.strip())
        else:
            failed += 1
            print("FAILED  " + title + ": " + r.stderr.strip())
    print(f"done: created {created}, skipped {skipped}, failed {failed}")
    return 0 if not failed else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("render").set_defaults(fn=cmd_render)
    sub.add_parser("urls").set_defaults(fn=cmd_urls)
    s = sub.add_parser("seed")
    s.add_argument("--repo", default=REPO)
    s.set_defaults(fn=cmd_seed)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
