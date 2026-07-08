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


def _normalize_title(t: str) -> str:
    """Lowercase and strip everything except alphanumerics — for fuzzy title matching."""
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def fetch_via_serpapi(key: str) -> tuple[tuple[int, int], dict[str, int]] | tuple[None, None]:
    """Return ((citations, h_index), {normalized_title: paper_citation_count}) or (None, None).

    A single Google Scholar Author API call returns both the author-level
    metrics AND the paginated list of papers with per-paper citation counts,
    so this costs one SerpApi request per page (author has <100 papers so
    almost always one request per run).
    """
    try:
        from serpapi import GoogleSearch  # type: ignore
    except ImportError:
        print("serpapi package not installed", file=sys.stderr)
        return None, None
    try:
        author_stats: tuple[int, int] | None = None
        papers: dict[str, int] = {}
        start = 0
        for _ in range(20):  # safety valve (>2000 papers)
            params = {
                "engine": "google_scholar_author",
                "author_id": SCHOLAR_USER_ID,
                "api_key": key,
                "hl": "en",
                "num": 100,
                "start": start,
            }
            res = GoogleSearch(params).get_dict()
            if "error" in res:
                print(f"SerpApi error: {res['error']}", file=sys.stderr)
                return None, None
            if author_stats is None:
                table = res.get("cited_by", {}).get("table", [])
                citations = int(table[0]["citations"]["all"])
                h_index = int(table[1]["h_index"]["all"])
                author_stats = (citations, h_index)
            articles = res.get("articles", []) or []
            if not articles:
                break
            for a in articles:
                title = a.get("title", "") or ""
                cites = int((a.get("cited_by") or {}).get("value", 0) or 0)
                papers[_normalize_title(title)] = cites
            if len(articles) < 100:
                break
            start += len(articles)
        return author_stats, papers
    except Exception as e:
        print(f"SerpApi fetch failed: {e}", file=sys.stderr)
        return None, None


def fetch_via_scholarly() -> tuple[tuple[int, int], dict[str, int]] | tuple[None, None]:
    """Fallback backend. Returns ((citations, h_index), {norm_title: cites})."""
    try:
        from scholarly import scholarly  # type: ignore
    except ImportError:
        print("scholarly package not installed", file=sys.stderr)
        return None, None
    try:
        author = scholarly.search_author_id(SCHOLAR_USER_ID)
        author = scholarly.fill(author, sections=["indices", "publications"])
        citations = int(author.get("citedby") or 0)
        h_index = int(author.get("hindex") or 0)
        if citations <= 0:
            print("scholarly returned zero citations, treating as failure", file=sys.stderr)
            return None, None
        papers: dict[str, int] = {}
        for p in author.get("publications", []) or []:
            title = (p.get("bib") or {}).get("title", "") or ""
            papers[_normalize_title(title)] = int(p.get("num_citations") or 0)
        return (citations, h_index), papers
    except Exception as e:
        print(f"scholarly fetch failed: {e}", file=sys.stderr)
        return None, None


def fetch_scholar_stats() -> tuple[tuple[int, int], dict[str, int]] | tuple[None, None]:
    """Try SerpApi first (if key set), else scholarly. Returns (stats, papers)."""
    key = os.environ.get("SERPAPI_KEY")
    if key:
        result = fetch_via_serpapi(key)
        if result[0] is not None:
            return result
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


def update_card_citations(html: str, papers: dict[str, int]) -> tuple[str, int]:
    """For every research-card line, look up its title in `papers` and inject or
    refresh a `data-citations="N"` attribute. Return (new_html, updated_count).

    Cards live one-per-line in index.html, so line-based rewriting is safe.
    We try exact normalized-title match first, then a 30-char prefix fallback
    for the occasional Scholar/site title mismatch.
    """
    lines = html.splitlines(keepends=True)
    updated = 0
    for i, line in enumerate(lines):
        if '<div class="card"' not in line or "card-title" not in line:
            continue
        m = re.search(r'card-title">([^<]+)', line)
        if not m:
            continue
        title_norm = _normalize_title(m.group(1))
        cites = papers.get(title_norm)
        if cites is None:
            for k, v in papers.items():
                if len(title_norm) >= 30 and len(k) >= 30 and title_norm[:30] == k[:30]:
                    cites = v
                    break
        if cites is None:
            continue
        if 'data-citations="' in line:
            new_line = re.sub(
                r'data-citations="\d+"', f'data-citations="{cites}"', line
            )
        else:
            new_line = line.replace(
                '<div class="card"',
                f'<div class="card" data-citations="{cites}"',
                1,
            )
        if new_line != line:
            lines[i] = new_line
            updated += 1
    return "".join(lines), updated


def main() -> int:
    result = fetch_scholar_stats()
    if result[0] is None:
        print("Scholar unreachable — leaving stats unchanged.", file=sys.stderr)
        return 0
    (citations, h_index), papers = result
    if citations <= 0:
        print("Scholar returned zero citations — treating as failure.", file=sys.stderr)
        return 0

    html = INDEX_HTML.read_text()
    now = datetime.now(timezone.utc)
    stamp = f"{now.strftime('%b')} {now.day}, {now.year}"  # e.g., "Jul 8, 2026"
    new_html = rewrite_html(html, citations, h_index, stamp)
    new_html, per_paper_updates = update_card_citations(new_html, papers)

    if new_html == html:
        print(
            f"No change (citations={citations}, h-index={h_index}, as of {stamp}, "
            f"per-paper considered={len(papers)})."
        )
        return 0

    INDEX_HTML.write_text(new_html)
    print(
        f"Updated: citations={citations}, h-index={h_index}, as of {stamp}, "
        f"per-paper cards refreshed={per_paper_updates}/{len(papers)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
