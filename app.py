#!/usr/bin/env python3
from __future__ import annotations

import csv
import io
import time
import traceback
import uuid
from dataclasses import asdict
from urllib.parse import urldefrag, urlsplit, urlunsplit

from flask import Flask, jsonify, render_template, request
from openpyxl import Workbook

from meokammap_crawler import HttpClient, MeokammapCrawler


app = Flask(__name__)
EXPORT_CACHE: dict[str, dict[str, list[dict]]] = {}
EXPORT_HEADERS = [
    "title",
    "autho",
    "summary",
    "tags",
    "completionStatus",
    "totalChaptersPlanned",
    "thumbnailUrl",
    "category",
    "chapterTitle",
    "nextChapterUrl",
    "chapterContent",
]


def _normalize_url_for_dedup(url: str) -> str:
    clean, _ = urldefrag((url or "").strip())
    if not clean:
        return ""
    parsed = urlsplit(clean)
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))


def _build_export_rows(items: list[dict], pair_results: list[dict]) -> list[dict]:
    if pair_results:
        merged_rows: list[dict] = []
        for pair in pair_results:
            story = pair.get("story") or {}
            chapters = pair.get("chapters") or []
            total = len(chapters)
            tags_list = story.get("genres") or []
            tags = " | ".join([str(x).strip() for x in tags_list if str(x).strip()])
            category = tags_list[0] if tags_list else ""
            base = {
                "title": story.get("title") or "",
                "autho": story.get("author") or "",
                "summary": story.get("summary") or "",
                "tags": tags,
                "completionStatus": story.get("status") or "",
                "totalChaptersPlanned": total,
                "thumbnailUrl": story.get("thumbnail") or "",
                "category": category or "",
            }
            if not chapters:
                merged_rows.append(
                    {**base, "chapterTitle": "", "nextChapterUrl": "", "chapterContent": ""}
                )
                continue
            last_idx = len(chapters) - 1
            for idx, ch in enumerate(chapters):
                merged_rows.append(
                    {
                        **base,
                        "chapterTitle": ch.get("title") or "",
                        "nextChapterUrl": (
                            (ch.get("nextChapterUrl") or ch.get("note") or "")
                            if idx == last_idx
                            else ""
                        ),
                        "chapterContent": ch.get("content") or "",
                    }
                )
        return merged_rows

    export_rows: list[dict] = []
    for item in items:
        if item.get("type") != "story":
            continue
        data = item.get("data") or {}
        chapters = data.get("chapters") or []
        genres = data.get("genres") or []
        tags = " | ".join([str(x).strip() for x in genres if str(x).strip()])
        category = genres[0] if genres else ""
        base = {
            "title": data.get("title") or "",
            "autho": data.get("author") or "",
            "summary": data.get("description") or "",
            "tags": tags,
            "completionStatus": data.get("status") or "",
            "totalChaptersPlanned": len(chapters),
            "thumbnailUrl": data.get("cover") or "",
            "category": category or "",
        }
        if not chapters:
            export_rows.append(
                {**base, "chapterTitle": "", "nextChapterUrl": "", "chapterContent": ""}
            )
            continue
        last_idx = len(chapters) - 1
        for idx, ch in enumerate(chapters):
            export_rows.append(
                {
                    **base,
                    "chapterTitle": ch.get("title") or "",
                    "nextChapterUrl": (
                        (ch.get("nextChapterUrl") or ch.get("note") or "")
                        if idx == last_idx
                        else ""
                    ),
                    "chapterContent": ch.get("content") or "",
                }
            )
    return export_rows


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/crawl")
def crawl():
    novel_url = (request.form.get("novel_url") or "").strip()
    fetch_content = request.form.get("fetch_content") == "on"
    max_chapters_raw = (request.form.get("max_chapters") or "").strip()
    delay_raw = (request.form.get("delay") or "0.6").strip()
    crawl_next_chapters = request.form.get("crawl_next_chapters") == "on"
    next_chapters_limit_raw = (request.form.get("next_chapters_limit") or "").strip()

    if not novel_url:
        return jsonify({"ok": False, "error": "Please input at least one URL."}), 400

    raw_lines = [line.strip() for line in novel_url.replace(",", "\n").splitlines() if line.strip()]
    story_urls: list[str] = []
    for line in raw_lines:
        # Backward compatibility: if user still inputs "story|chapter", keep only story side.
        if "|" in line:
            line = line.split("|", 1)[0].strip()
        if line:
            story_urls.append(line)
    if not story_urls:
        return jsonify({"ok": False, "error": "Please input at least one valid story URL."}), 400

    try:
        max_chapters = int(max_chapters_raw) if max_chapters_raw else 0
        if max_chapters < 0:
            return jsonify({"ok": False, "error": "max_chapters must be >= 0"}), 400
    except ValueError:
        return jsonify({"ok": False, "error": "max_chapters must be an integer"}), 400

    try:
        delay = float(delay_raw)
        if delay < 0:
            return jsonify({"ok": False, "error": "delay must be >= 0"}), 400
    except ValueError:
        return jsonify({"ok": False, "error": "delay must be a number"}), 400

    try:
        next_chapters_limit = int(next_chapters_limit_raw) if next_chapters_limit_raw else 0
        if next_chapters_limit < 0:
            return jsonify({"ok": False, "error": "next_chapters_limit must be >= 0"}), 400
    except ValueError:
        return jsonify({"ok": False, "error": "next_chapters_limit must be an integer"}), 400

    try:
        client = HttpClient(use_cloudscraper=True)
        crawler = MeokammapCrawler(client=client, delay=delay)
        rows: list[dict] = []
        items: list[dict] = []
        pair_result: dict | None = None
        pair_results: list[dict] = []
        for story_index, story_url in enumerate(story_urls, start=1):
            story_brief = crawler.parse_story_brief(story_url)
            story_full = crawler.parse_story(story_url)

            chapter_candidates: list[tuple[int, str]] = []
            if story_full.chapters:
                for ch in story_full.chapters:
                    idx = ch.index
                    if idx is None:
                        idx = crawler.extract_chapter_index_from_url(ch.url)
                    if idx is None:
                        continue
                    chapter_candidates.append((idx, ch.url))
                chapter_candidates.sort(key=lambda item: item[0])

            # Add generated fallback candidates to recover cases where detected links fail.
            if crawl_next_chapters:
                if next_chapters_limit > 0:
                    fallback_count = max(next_chapters_limit * 3, 10)
                elif chapter_candidates:
                    fallback_count = 0
                else:
                    fallback_count = 30
            else:
                fallback_count = 1
            for idx in range(1, fallback_count + 1):
                guessed = crawler.generate_chapter_url_from_story(story_url, idx)
                if guessed:
                    chapter_candidates.append((idx, guessed))

            # Sort by index and deduplicate while preserving order.
            chapter_candidates.sort(key=lambda item: item[0])

            chapters_payload = []
            seen_urls = set()
            for _, chapter_candidate_url in chapter_candidates:
                if not crawl_next_chapters and chapters_payload:
                    break
                if next_chapters_limit > 0 and len(chapters_payload) >= next_chapters_limit:
                    break
                normalized_candidate = _normalize_url_for_dedup(chapter_candidate_url)
                if not normalized_candidate or normalized_candidate in seen_urls:
                    continue
                seen_urls.add(normalized_candidate)
                try:
                    full = crawler.parse_chapter_content(chapter_candidate_url)
                except Exception:
                    # Skip broken candidate and continue other chapter URLs.
                    continue
                if not full.content:
                    continue
                chapters_payload.append(
                    {
                        "title": full.title,
                        "content": full.content,
                        "source_url": full.url,
                        "nextChapterUrl": full.note or "",
                    }
                )
                rows.append(
                    {
                        "source_url": full.url,
                        "type": "chapter",
                        "story_title": story_brief.title or "",
                        "story_url": story_url,
                        "chapter_title": full.title or "",
                        "chapter_url": full.url or "",
                        "content": full.content or "",
                    }
                )
                items.append({"type": "chapter", "source_url": full.url, "data": asdict(full)})
                if delay > 0:
                    time.sleep(delay)

            first_chapter = chapters_payload[0] if chapters_payload else {"title": "", "content": "", "source_url": ""}
            current_pair = {
                "pair_index": story_index,
                "story": {
                    "thumbnail": story_brief.thumbnail,
                    "title": story_brief.title,
                    "genres": story_brief.genres,
                    "status": story_brief.status,
                    "summary": story_brief.summary,
                    "author": story_brief.author,
                    "source_url": story_brief.source_url,
                },
                "chapter": {
                    "title": first_chapter.get("title"),
                    "content": first_chapter.get("content"),
                    "source_url": first_chapter.get("source_url"),
                },
                "chapters": chapters_payload,
                "source_urls": [story_url],
            }
            pair_results.append(current_pair)
            items.append({"type": "story_brief", "source_url": story_url, "data": current_pair["story"]})
            rows.append(
                {
                    "source_url": story_url,
                    "type": "story_brief",
                    "story_title": story_brief.title or "",
                    "story_url": story_url,
                    "chapter_title": "",
                    "chapter_url": "",
                    "content": story_brief.summary or "",
                }
            )

        pair_result = pair_results[0] if pair_results else None

        export_rows = _build_export_rows(items, pair_results)
        export_id = str(uuid.uuid4())
        EXPORT_CACHE[export_id] = {"rows": rows, "export_rows": export_rows}
        return jsonify(
            {
                "ok": True,
                "items": items,
                "rows": rows,
                "export_rows": export_rows,
                "pair_result": pair_result,
                "pair_results": pair_results,
                "download_url": f"/download-export/{export_id}?format=xlsx",
                "download_csv_url": f"/download-export/{export_id}?format=csv",
                "download_xlsx_url": f"/download-export/{export_id}?format=xlsx",
            }
        )
    except Exception as exc:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": str(exc),
                    "trace": traceback.format_exc(limit=2),
                }
            ),
            500,
        )


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/download-export/<export_id>")
def download_export(export_id: str):
    cached = EXPORT_CACHE.get(export_id)
    if cached is None:
        return jsonify({"ok": False, "error": "Export not found or expired."}), 404
    export_rows = cached.get("export_rows") or []
    fmt = (request.args.get("format") or "xlsx").strip().lower()

    if fmt == "csv":
        text_stream = io.StringIO()
        writer = csv.DictWriter(text_stream, fieldnames=EXPORT_HEADERS)
        writer.writeheader()
        for row in export_rows:
            writer.writerow({k: row.get(k, "") for k in EXPORT_HEADERS})
        from flask import Response

        return Response(
            text_stream.getvalue(),
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=crawl_export.csv"},
        )

    wb = Workbook()
    ws = wb.active
    ws.title = "export"
    ws.append(EXPORT_HEADERS)
    for row in export_rows:
        ws.append([row.get(h, "") for h in EXPORT_HEADERS])

    widths = {
        "A": 28,
        "B": 22,
        "C": 64,
        "D": 36,
        "E": 22,
        "F": 22,
        "G": 44,
        "H": 24,
        "I": 28,
        "J": 100,
        "K": 56,
    }
    for col, width in widths.items():
        ws.column_dimensions[col].width = width

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)

    from flask import Response

    return Response(
        stream.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=crawl_export.xlsx"},
    )


@app.get("/download-excel/<export_id>")
def download_excel_legacy(export_id: str):
    return download_export(export_id)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
