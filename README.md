# meokammap.com crawler

Simple Python tool to crawl story metadata and chapter content from `https://meokammap.com/`.

## 1) Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2) Usage

### A. Get story links from a listing page

```bash
python meokammap_crawler.py \
  --list "https://meokammap.com/" \
  --output story_links.json
```

### B. Get story metadata + chapter list

```bash
python meokammap_crawler.py \
  --story "https://meokammap.com/your-story-url/" \
  --output story.json
```

### C. Get full content from one chapter

```bash
python meokammap_crawler.py \
  --chapter "https://meokammap.com/your-chapter-url/" \
  --output chapter.json
```

### D. Get full content for story chapters

```bash
python meokammap_crawler.py \
  --story "https://meokammap.com/your-story-url/" \
  --fetch-content \
  --max-chapters 10 \
  --delay 0.8 \
  --output story_with_content.json
```

## Notes

- The site may use anti-bot protection. Tool includes optional `cloudscraper` support automatically.
- If you want pure `requests`, add `--no-cloudscraper`.
- Keep crawling rate low (`--delay`) to avoid overloading the site.

## Web App (input novel link only)

Run:

```bash
python app.py
```

Then open:

- `http://127.0.0.1:5000`

In the web UI, you only need to paste the novel URL and click **Crawl**.

- If URL is chapter-style (`/read/...` or `/chuong-...`), app returns chapter content directly.
- If URL is `https://nangtho.site/truyen/...`, app auto-discovers chapter links and crawls chapter contents.
- You can paste multiple URLs (one per line or comma-separated).
- Output is shown in table format and can be downloaded as Excel (`.xlsx`).

## GitHub Pages

The crawler UI (`index.html` at the repo root) is published by GitHub Pages from the `main` branch root (`.nojekyll` keeps Jekyll from rendering this README instead).

- URL: `https://lamtrang2405.github.io/web-scraper/`
- GitHub Pages only hosts the UI. Crawling still runs on the Flask API, so start `python app.py` on your machine; the page calls `http://127.0.0.1:5000` by default.
- To use another API host, change the **Crawler API URL** field (saved in the browser) or open `https://lamtrang2405.github.io/web-scraper/?api=https://your-api-host`.
- The API only accepts cross-origin calls from origins listed in `CORS_ALLOWED_ORIGINS` (comma-separated, default `https://lamtrang2405.github.io`).
