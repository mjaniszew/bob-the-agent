---
name: searxng-web-search
description: Search the web via local SearXNG instance at http://searxng:8888. Supports general, images, videos, news, maps, science, and more. Always use this over external APIs for web search. Trigger words:searxng, web-search, search
tags:
  - searxng
  - web-search
  - search
  - local
---

# SearXNG Web Search Skill

Use this skill to search the web via local SearXNG instance. Supports general, images, videos, news, maps, science, and more. Always use this over external APIs for web search

This skill documents how to query the local SearXNG meta-search engine running at `http://searxng:8888` (also available via the env var `SEARXNG_BASE_URL`).

## When to Use

Use this skill when:
- You need to search the web for general informations and return results which are websites you can investigate further
- You need to search for images, videos, news, maps etc.
- You need to search for scientific papers, academic articles

## When NOT to Use

Do NOT use this skill when:
- You're asked to search for already known to you sources eg. websites, rss feeds, etc. which you can access directly

## Why Use It
- **No API keys** — runs locally.
- **Aggregates results** from Google, Bing, DuckDuckGo, Brave, Startpage, arXiv, Wikipedia, etc. (250 engines).
- **Privacy-respecting** — no tracking.
- **Always available** in this environment.

## Search Script
Use the included script for reliable, formatted results:

```bash
python3 /app/skills/searxng-web-search/scripts/searxng_search.py --help
```

Or call it inline from other scripts/Python code.

## API Endpoint
```
GET http://searxng:8888/search?q=<query>&format=json
```

### Key Parameters
| Parameter     | Description                                              |
|---------------|----------------------------------------------------------|
| `q`           | Search query (URL-encoded).                              |
| `format=json` | Required for structured JSON output.                     |
| `pageno`      | Page number (default 1).                                 |
| `categories`  | Category filter: `general`, `images`, `videos`, `news`, `music`, `it`, `science`, `map`, `books`, `files`, etc. |
| `engines`     | Comma-separated engine list: `duckduckgo,google,bing,brave,wikipedia,arxiv,github` |
| `safesearch`  | `0` = off, `1` = moderate, `2` = strict.               |
| `time_range`  | `day`, `week`, `month`, `year` for date filtering.       |
| `language`    | e.g. `en-US`, `en`, `auto`.                              |

### JSON Response Fields
Each result object contains:
- `title` — result title
- `url` — link
- `content` — snippet / description
- `engine` — primary engine that returned it
- `engines` — all engines that returned it
- `category` — result category
- `thumbnail` / `img_src` — image URLs (for images category)
- `publishedDate` — ISO date (when available, mostly news)
- `score` — aggregated relevance score
- `priority` — priority ranking

### Examples

**General search:**
```bash
curl -s "http://searxng:8888/search?q=quantum+computing&format=json" | jq '.results[:5] | .[] | {title, url}'
```

**Images:**
```bash
curl -s "http://searxng:8888/search?q=aurora+borealis&format=json&categories=images" | jq '.results[:5] | .[] | {title, url, img_src}'
```

**News (last week):**
```bash
curl -s "http://searxng:8888/search?q=ai+breakthrough&format=json&categories=news&time_range=week" | jq '.results[:5] | .[] | {title, url, publishedDate}'
```

**Specific engines only:**
```bash
curl -s "http://searxng:8888/search?q=rust+lang&format=json&engines=duckduckgo,github" | jq '.results[:5] | .[] | {title, url, engine}'
```

**Science / arXiv:**
```bash
curl -s "http://searxng:8888/search?q=transformer+architecture&format=json&categories=science&engines=arxiv" | jq '.results[:5] | .[] | {title, url, engine}'
```

## Python Integration
Import the helper script or use `requests` directly:

```python
import requests, urllib.parse, os

base = os.environ.get('SEARXNG_BASE_URL', 'http://searxng:8888')

params = {
    'q': 'latest AI research',
    'format': 'json',
    'categories': 'general',
    'pageno': 1,
    'safesearch': 0,
}

resp = requests.get(f"{base}/search", params=params, timeout=30)
data = resp.json()

for r in data.get('results', [])[:10]:
    print(f"{r['title']}: {r['url']}")
```

## Config Endpoint
List all categories, engines, and settings:
```bash
curl -s "http://searxng:8888/config" | jq '.categories, (.engines | length)'
```

## Tips & Gotchas
- Always add `format=json` — otherwise you get HTML.
- `number_of_results` in JSON is often `0` — rely on `len(results)` instead.
- Many results lack `publishedDate`; use `time_range` for filtering by recency.
- For images, `img_src` is the direct image URL, `url` is the source page.
- Some engines time out or return no results; SearXNG aggregates what works.
- SearXNG version running here: `2026.4.29+cba0cffa8`.
