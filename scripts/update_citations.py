#!/usr/bin/env python3
"""Fetch citation count + h-index from Google Scholar and refresh the
hardcoded dashboard stats in index.html. Designed for GitHub Actions cron.

If the fetch fails (Google Scholar blocks the runner, network error, etc.)
the script exits 0 without modifying anything, so the cron simply skips
that run and leaves the previously committed numbers in place.

Backends (tried in order):
  * scholarly (free, may be blocked by Google on datacenter IPs)
  * SerpApi (reliable) — enabled if SERPAPI_KEY is set
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHOLAR_USER_ID = "A7gPbV8AAAAJ"
ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = ROOT / "index.html"


def fetch_via_serpapi(key: str) -> tuple[int, int] | tuple[None, None]:
    try:
        from serpapi import GoogleSearch  # type: ignore
    except ImportError:
        print("serpapi package not installed", file=sys.stderr)
        return None, None
    try:
        params = {
            "engine": "google_scholar_author",
            "author_id": SCHOLAR_USER_ID,
            "api_key": key,
            "hl": "en",
            "num": 20,
        }
        res = GoogleSearch(params).get_dict()
        if "error" in res:
            print(f"SerpApi error: {res['error']}", file=sys.stderr)
            return None, None
        # cited_by.table[0].citations.all is the total citation count
        # cited_by.table[1].h_index.all is the h-index
        table = res.get("cited_by", {}).get("table", [])
        citations = int(table[0]["citations"]["all"])
        h_index = int(table[1]["h_index"]["all"])
        return citations, h_index
    except Exception as e:
        print(f"SerpApi fetch failed: {e}", file=sys.stderr)
        return None, None


def fetch_via_scholarly() -> tuple[int, int] | tuple[None, None]:
    try:
        from scholarly import scholarly  # type: ignore
    except ImportError:
        print("scholarly package not installed", file=sys.stderr)
        return None, None
    try:
        author = scholarly.search_author_id(SCHOLAR_USER_ID)
        author = scholarly.fill(author, sections=["indices"])
        citations = int(author.get("citedby") or 0)
        h_index = int(author.get("hindex") or 0)
        if citations <= 0:
            print("scholarly returned zero citations, treating as failure", file=sys.stderr)
            return None, None
        return citations, h_index
    except Exception as e:
        print(f"scholarly fetch failed: {e}", file=sys.stderr)
        return None, None


def fetch_scholar_stats() -> tuple[int, int] | tuple[None, None]:
    key = os.environ.get("SERPAPI_KEY")
    if key:
        cit, h = fetch_via_serpapi(key)
        if cit is not None:
            return cit, h
        print("SerpApi failed, falling back to scholarly", file=sys.stderr)
    return fetch_via_scholarly()


def rewrite_html(html: str, citations: int, h_index: int, stamp: str) -> str:
    """Replace the hardcoded stat values + as-of date, preserving surrounding markup."""
    new_html = re.sub(
        r'(<div class="stat-num" id="stat-citations">)\d+(</div>)',
        rf"\g<1>{citations}\g<2>",
        html,
    )
    new_html = re.sub(
        r'(<div class="stat-num" id="stat-hindex">)\d+(</div>)',
        rf"\g<1>{h_index}\g<2>",
        new_html,
    )
    new_html = re.sub(
        r"(Google Scholar &middot; as of )[A-Za-z]+ (?:\d{1,2}, )?\d{4}",
        rf"\g<1>{stamp}",
        new_html,
    )
    return new_html


def main() -> int:
    citations, h_index = fetch_scholar_stats()
    if citations is None or citations <= 0:
        print("Scholar unreachable — leaving stats unchanged.", file=sys.stderr)
        return 0

    html = INDEX_HTML.read_text()
    now = datetime.now(timezone.utc)
    stamp = f"{now.strftime('%b')} {now.day}, {now.year}"  # e.g., "Jul 8, 2026"
    new_html = rewrite_html(html, citations, h_index, stamp)

    if new_html == html:
        print(f"No change (citations={citations}, h-index={h_index}, as of {stamp}).")
        return 0

    INDEX_HTML.write_text(new_html)
    print(f"Updated: citations={citations}, h-index={h_index}, as of {stamp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
