#!/usr/bin/env python3
"""
SearXNG Web Search CLI Helper (stdlib only — no external deps)

Usage:
    python3 searxng_search.py "quantum computing"
    python3 searxng_search.py "cat pictures" --category images --limit 10
    python3 searxng_search.py "tech news" --category news --time-range week --limit 5
    python3 searxng_search.py "python" --engines duckduckgo,google --json

Environment:
    SEARXNG_BASE_URL — defaults to http://searxng:8888
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = os.environ.get("SEARXNG_BASE_URL", "http://searxng:8888")


def search(
    query: str,
    base_url: str = DEFAULT_BASE_URL,
    category: str = "general",
    engines: str | None = None,
    time_range: str | None = None,
    language: str | None = None,
    safesearch: int = 0,
    pageno: int = 1,
    limit: int = 10,
    timeout: int = 30,
):
    params = {
        "q": query,
        "format": "json",
        "categories": category,
        "pageno": pageno,
        "safesearch": safesearch,
    }
    if engines:
        params["engines"] = engines
    if time_range:
        params["time_range"] = time_range
    if language:
        params["language"] = language

    query_string = urllib.parse.urlencode(params)
    url = f"{base_url.rstrip('/')}/search?{query_string}"

    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    results = data.get("results", [])
    return results[:limit]


def format_text(results: list[dict]) -> str:
    lines = []
    for i, r in enumerate(results, start=1):
        title = r.get("title", "(no title)")
        url = r.get("url", "(no url)")
        content = r.get("content", "")
        engine = r.get("engine", "")
        engines = r.get("engines", [])
        published = r.get("publishedDate", "")

        lines.append(f"[{i}] {title}")
        lines.append(f"    URL: {url}")
        if content:
            snippet = content.replace("\n", " ")[:300]
            lines.append(f"    Snippet: {snippet}")
        if published:
            lines.append(f"    Published: {published}")
        if engines:
            lines.append(f"    Engines: {', '.join(engines)}")
        elif engine:
            lines.append(f"    Engine: {engine}")
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Search via local SearXNG")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="SearXNG base URL")
    parser.add_argument("--category", default="general", help="Category (general, images, videos, news, ...)")
    parser.add_argument("--engines", default=None, help="Comma-separated engine list")
    parser.add_argument("--time-range", default=None, help="day / week / month / year")
    parser.add_argument("--language", default=None, help="Language code, e.g. en-US")
    parser.add_argument("--safesearch", type=int, default=0, help="0=off, 1=moderate, 2=strict")
    parser.add_argument("--pageno", type=int, default=1, help="Page number")
    parser.add_argument("--limit", type=int, default=10, help="Max results to show")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    results = search(
        query=args.query,
        base_url=args.base_url,
        category=args.category,
        engines=args.engines,
        time_range=args.time_range,
        language=args.language,
        safesearch=args.safesearch,
        pageno=args.pageno,
        limit=args.limit,
    )

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        if not results:
            print("No results found.")
            sys.exit(1)
        print(format_text(results))


if __name__ == "__main__":
    main()
