#!/usr/bin/env python3
"""
Crawler for meokammap.com story content.

Features:
- Search/list story links from category/listing pages
- Crawl story metadata
- Crawl chapter list
- Crawl chapter content
- Export JSON output
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from typing import Any, Iterable, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
}


@dataclass
class Chapter:
    title: str
    url: str
    index: Optional[int] = None
    content: Optional[str] = None
    note: Optional[str] = None


@dataclass
class Story:
    title: str
    url: str
    author: Optional[str] = None
    status: Optional[str] = None
    genres: Optional[list[str]] = None
    description: Optional[str] = None
    cover: Optional[str] = None
    chapters: Optional[list[Chapter]] = None


@dataclass
class StoryBrief:
    thumbnail: Optional[str]
    title: Optional[str]
    genres: list[str]
    status: Optional[str]
    summary: Optional[str]
    author: Optional[str]
    source_url: str


class HttpClient:
    def __init__(self, timeout: int = 25, use_cloudscraper: bool = True):
        self.timeout = timeout
        self.session = self._build_session(use_cloudscraper)

    def _build_session(self, use_cloudscraper: bool):
        if use_cloudscraper:
            try:
                import cloudscraper  # type: ignore

                scraper = cloudscraper.create_scraper()
                scraper.headers.update(DEFAULT_HEADERS)
                return scraper
            except Exception:
                pass

        session = requests.Session()
        session.headers.update(DEFAULT_HEADERS)
        return session

    def get(self, url: str, retries: int = 3, sleep_s: float = 1.2) -> str:
        last_error = None
        for i in range(retries):
            try:
                res = self.session.get(url, timeout=self.timeout)
                res.raise_for_status()
                return res.text
            except Exception as err:
                last_error = err
                if i < retries - 1:
                    time.sleep(sleep_s)
        raise RuntimeError(f"Request failed for {url}: {last_error}")


class MeokammapCrawler:
    def __init__(self, client: HttpClient, delay: float = 0.6):
        self.client = client
        self.delay = delay

    def _soup(self, url: str) -> BeautifulSoup:
        html = self.client.get(url)
        return BeautifulSoup(html, "html.parser")

    @staticmethod
    def _host(url: str) -> str:
        return urlparse(url).netloc.lower()

    @staticmethod
    def _looks_like_chapter_url(url: str) -> bool:
        lower = url.lower()
        return "/chuong-" in lower or re.search(
            r"/(chapter|chap|ch|chuong)[-/]",
            lower,
            re.IGNORECASE,
        ) is not None

    @staticmethod
    def extract_chapter_index_from_url(url: str) -> Optional[int]:
        match = re.search(r"(?:chuong|chapter|chap|ch)-(\d+)(?:/)?$", url, re.IGNORECASE)
        if not match:
            match = re.search(r"/(\d+)(?:/)?$", url)
        if not match:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    @staticmethod
    def replace_chapter_index_in_url(url: str, new_index: int) -> Optional[str]:
        if new_index <= 0:
            return None
        updated = re.sub(
            r"((?:chuong|chapter|chap|ch)-)\d+(/?)$",
            rf"\g<1>{new_index}\2",
            url,
            flags=re.IGNORECASE,
        )
        if updated != url:
            return updated
        return None

    @staticmethod
    def generate_chapter_url_from_story(story_url: str, chapter_index: int) -> Optional[str]:
        if chapter_index <= 0:
            return None
        base = story_url.strip()
        if not base:
            return None
        base = base.rstrip("/")
        return f"{base}/chuong-{chapter_index}/"

    def discover_story_links(self, page_url: str) -> list[str]:
        soup = self._soup(page_url)
        links = []

        # Collect anchors likely pointing to stories.
        for a in soup.select("a[href]"):
            href = a.get("href", "").strip()
            if not href:
                continue
            full = urljoin(page_url, href)
            # Heuristic for story links.
            if re.search(r"/(truyen|manga|novel)/", full, re.IGNORECASE):
                links.append(full)

        # Fallback: keep domain-local links with title-ish anchor text.
        if not links:
            for a in soup.select("a[href]"):
                text = a.get_text(" ", strip=True)
                href = a.get("href", "").strip()
                if len(text) >= 4 and href.startswith("/"):
                    links.append(urljoin(page_url, href))

        # Unique while preserving order.
        seen = set()
        result = []
        for link in links:
            if link not in seen:
                seen.add(link)
                result.append(link)
        return result

    def parse_story(self, story_url: str) -> Story:
        if "vivutruyen2.net" in self._host(story_url):
            return self._parse_story_vivutruyen(story_url)

        soup = self._soup(story_url)

        title = self._first_text(
            soup,
            [
                "h1",
                ".entry-title",
                ".post-title",
                ".book-title",
                ".novel-title",
            ],
        ) or "Unknown title"

        author = self._extract_value_by_label(soup, ["author", "tác giả"])
        status = self._extract_value_by_label(soup, ["status", "trạng thái"])

        genres = self._extract_genres(soup)
        description = self._first_text(
            soup,
            [
                ".summary",
                ".description",
                ".entry-content",
                ".book-summary-content",
                "#manga-description",
            ],
        )
        cover = self._first_attr(
            soup,
            [
                ".summary_image img",
                ".book-cover img",
                ".info-image img",
                ".wp-post-image",
                "img",
            ],
            "src",
        )
        if cover:
            cover = urljoin(story_url, cover)

        chapters = self.parse_chapter_list(soup, story_url)
        return Story(
            title=title,
            url=story_url,
            author=author,
            status=status,
            genres=genres if genres else None,
            description=description,
            cover=cover,
            chapters=chapters if chapters else None,
        )

    def _parse_story_vivutruyen(self, story_url: str) -> Story:
        soup = self._soup(story_url)
        brief = self.parse_story_brief(story_url, soup=soup)
        chapters = self.parse_chapter_list(soup, story_url)
        return Story(
            title=brief.title or "Unknown title",
            url=story_url,
            author=brief.author,
            status=brief.status,
            genres=brief.genres or None,
            description=brief.summary,
            cover=brief.thumbnail,
            chapters=chapters if chapters else None,
        )

    def parse_story_brief(
        self, story_url: str, soup: Optional[BeautifulSoup] = None
    ) -> StoryBrief:
        if "vivutruyen2.net" in self._host(story_url):
            return self._parse_story_brief_vivutruyen(story_url, soup=soup)
        story = self.parse_story(story_url)
        return StoryBrief(
            thumbnail=story.cover,
            title=story.title,
            genres=story.genres or [],
            status=story.status,
            summary=story.description,
            author=story.author,
            source_url=story_url,
        )

    def _parse_story_brief_vivutruyen(
        self, story_url: str, soup: Optional[BeautifulSoup] = None
    ) -> StoryBrief:
        soup = soup or self._soup(story_url)
        lines = self._normalized_lines(soup.get_text("\n", strip=True))

        title = self._first_text(soup, ["h1", ".entry-title", ".post-title"])
        if not title:
            title = self._first_text(soup, ["meta[property='og:title']"])

        thumbnail = self._first_attr(
            soup,
            [
                "meta[property='og:image']",
                ".summary_image img",
                ".book-cover img",
                ".entry-content img",
                "img",
            ],
            "content",
        )
        if not thumbnail:
            thumbnail = self._first_attr(
                soup,
                [
                    ".summary_image img",
                    ".book-cover img",
                    ".entry-content img",
                    "img",
                ],
                "src",
            )
        if thumbnail:
            thumbnail = urljoin(story_url, thumbnail)

        genres = self._extract_genres_vivutruyen(soup, lines)
        status = self._extract_vivutruyen_labeled_value(lines, "trạng thái")
        author = self._extract_vivutruyen_labeled_value(lines, "tác giả")

        summary = self._extract_vivutruyen_summary(lines)
        if not summary:
            summary = self._first_attr(
                soup, ["meta[name='description']", "meta[property='og:description']"], "content"
            )

        return StoryBrief(
            thumbnail=thumbnail,
            title=title,
            genres=genres,
            status=status,
            summary=summary,
            author=author,
            source_url=story_url,
        )

    def parse_chapter_list(
        self, soup_or_url: BeautifulSoup | str, base_url: Optional[str] = None
    ) -> list[Chapter]:
        if isinstance(soup_or_url, str):
            url = soup_or_url
            soup = self._soup(url)
            base = url
        else:
            soup = soup_or_url
            base = base_url or ""

        candidates = []
        selectors = [
            ".wp-manga-chapter a",
            ".chapter-list a",
            ".chapters a",
            "li.chapter a",
            "a[href*='chapter']",
            "a[href*='chuong-']",
        ]
        for sel in selectors:
            for a in soup.select(sel):
                href = a.get("href", "").strip()
                text = a.get_text(" ", strip=True)
                if href and text:
                    candidates.append((text, urljoin(base, href)))

        chapters = []
        seen = set()
        for text, href in candidates:
            if href in seen:
                continue
            # Keep only chapter-like links.
            if not re.search(r"/(chapter|chap|ch|chuong)[-/]", href, re.IGNORECASE):
                continue
            seen.add(href)
            idx = self._extract_chapter_index(text)
            chapters.append(Chapter(title=text, url=href, index=idx))

        # Usually chapter list is newest-first; sort by index when present.
        if chapters and any(ch.index is not None for ch in chapters):
            chapters.sort(key=lambda c: (c.index is None, c.index or 10**9))

        return chapters

    def parse_chapter_content(self, chapter_url: str) -> Chapter:
        if "vivutruyen2.net" in self._host(chapter_url):
            return self._parse_chapter_content_vivutruyen(chapter_url)

        soup = self._soup(chapter_url)
        title = self._first_text(
            soup,
            ["h1", ".chapter-title", ".reading-content h2", ".entry-title"],
        ) or "Untitled chapter"

        content = None
        content_selectors = [
            ".reading-content",
            ".chapter-content",
            ".entry-content",
            ".text-left",
            "article",
        ]
        for sel in content_selectors:
            node = soup.select_one(sel)
            if node:
                txt = node.get_text("\n", strip=True)
                txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
                if len(txt) > 50:
                    content = self._clean_chapter_content(txt)
                    break

        # Fallback: try to detect the largest text block in article/main/body.
        if not content:
            scope = soup.select_one("article") or soup.select_one("main") or soup.body
            if scope:
                # Remove noisy nodes before collecting text.
                for noisy in scope.select(
                    "script, style, nav, header, footer, form, button, noscript"
                ):
                    noisy.decompose()

                blocks = []
                for node in scope.select("p, div"):
                    text = node.get_text(" ", strip=True)
                    text = re.sub(r"\s+", " ", text).strip()
                    if len(text) >= 80:
                        blocks.append(text)

                if blocks:
                    # Pick several longest blocks to avoid menus/labels.
                    blocks.sort(key=len, reverse=True)
                    merged = "\n\n".join(blocks[:8]).strip()
                    if len(merged) > 100:
                        content = self._clean_chapter_content(merged)

        next_direct_url = self._extract_direct_next_chapter_url(soup, chapter_url)
        if not next_direct_url and content:
            next_direct_url = self._extract_next_chapter_url_from_text(content, chapter_url)
        return Chapter(title=title, url=chapter_url, content=content, note=next_direct_url)

    def _parse_chapter_content_vivutruyen(self, chapter_url: str) -> Chapter:
        soup = self._soup(chapter_url)
        article = soup.select_one("article") or soup
        lines = self._normalized_lines(article.get_text("\n", strip=True))
        title = self._first_text(
            soup,
            ["h1", "h2", ".chapter-title", ".entry-title"],
        ) or "Untitled chapter"
        if title == "Untitled chapter":
            for line in lines:
                if re.search(r"^chương\s+\d+", line, re.IGNORECASE):
                    title = line
                    break

        start_idx = 0
        for idx, line in enumerate(lines):
            if line.strip().lower() == title.strip().lower():
                start_idx = idx + 1
                break

        stop_markers = [
            "website đang trong quá trình thử nghiệm",
            "mời bạn click vào liên kết bên dưới",
            "scroll up",
        ]
        chunks = []
        for line in lines[start_idx:]:
            low = line.lower()
            if any(marker in low for marker in stop_markers):
                break
            if low in {"prev", "next", "prev next"}:
                continue
            if self._is_vivutruyen_noise_line(line):
                continue
            if len(line) < 3:
                continue
            chunks.append(line)

        content = self._clean_vivutruyen_chapter_content("\n".join(chunks)) if chunks else None
        next_direct_url = self._extract_direct_next_chapter_url(soup, chapter_url)
        if not next_direct_url and content:
            next_direct_url = self._extract_next_chapter_url_from_text(content, chapter_url)
        return Chapter(title=title, url=chapter_url, content=content, note=next_direct_url)

    def _extract_direct_next_chapter_url(self, soup: BeautifulSoup, current_url: str) -> Optional[str]:
        current_normalized = _normalize_url_for_compare(current_url)
        current_idx = self.extract_chapter_index_from_url(current_url)
        chapter_candidates: list[tuple[Optional[int], str, str]] = []
        for a in soup.select("a[href]"):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            full = urljoin(current_url, href)
            normalized_full = _normalize_url_for_compare(full)
            if not normalized_full or normalized_full == current_normalized:
                continue
            if not self._looks_like_chapter_url(normalized_full):
                continue
            text = a.get_text(" ", strip=True).lower()
            idx = self.extract_chapter_index_from_url(normalized_full)
            chapter_candidates.append((idx, normalized_full, text))
            if current_idx is not None and idx == current_idx + 1:
                return normalized_full
            if any(
                marker in text
                for marker in [
                    "chương tiếp",
                    "chuong tiep",
                    "chương sau",
                    "chuong sau",
                    "chapter next",
                    "next chapter",
                    "xem tiếp",
                    "xem tiep",
                    "liên kết bên dưới",
                    "lien ket ben duoi",
                    "bên dưới",
                    "ben duoi",
                    "next",
                ]
            ):
                return normalized_full
        if current_idx is not None:
            bigger = [
                (idx, url)
                for idx, url, _ in chapter_candidates
                if idx is not None and idx > current_idx
            ]
            if bigger:
                bigger.sort(key=lambda x: x[0])
                return bigger[0][1]
        return None

    def _extract_next_chapter_url_from_text(self, text: str, current_url: str) -> Optional[str]:
        current_normalized = _normalize_url_for_compare(current_url)
        current_idx = self.extract_chapter_index_from_url(current_url)
        url_pattern = re.compile(r"https?://[^\s)>\"]+")
        candidates: list[tuple[Optional[int], str]] = []
        for raw in url_pattern.findall(text):
            normalized = _normalize_url_for_compare(raw)
            if not normalized or normalized == current_normalized:
                continue
            if not self._looks_like_chapter_url(normalized):
                continue
            idx = self.extract_chapter_index_from_url(normalized)
            if current_idx is not None and idx == current_idx + 1:
                return normalized
            candidates.append((idx, normalized))
        if current_idx is not None:
            bigger = [(idx, url) for idx, url in candidates if idx is not None and idx > current_idx]
            if bigger:
                bigger.sort(key=lambda x: x[0])
                return bigger[0][1]
        return candidates[0][1] if candidates else None

    @staticmethod
    def _first_text(soup: BeautifulSoup, selectors: list[str]) -> Optional[str]:
        for sel in selectors:
            node = soup.select_one(sel)
            if node:
                text = node.get_text(" ", strip=True)
                if text:
                    return text
        return None

    @staticmethod
    def _first_attr(
        soup: BeautifulSoup, selectors: list[str], attr: str
    ) -> Optional[str]:
        for sel in selectors:
            node = soup.select_one(sel)
            if node and node.has_attr(attr):
                value = str(node.get(attr)).strip()
                if value:
                    return value
        return None

    @staticmethod
    def _extract_value_by_label(soup: BeautifulSoup, labels: Iterable[str]) -> Optional[str]:
        labels_lower = [lb.lower() for lb in labels]

        # Generic search in list items/divs, e.g. "Author: Foo"
        for node in soup.select("li, div, span, p"):
            text = node.get_text(" ", strip=True)
            if not text:
                continue
            low = text.lower()
            for lb in labels_lower:
                if lb in low and ":" in text:
                    parts = text.split(":", 1)
                    if len(parts) > 1 and parts[1].strip():
                        return parts[1].strip()
        return None

    @staticmethod
    def _extract_genres(soup: BeautifulSoup) -> list[str]:
        genres = []
        for a in soup.select("a[href*='genre'], a[href*='the-loai'], .genres a"):
            text = a.get_text(" ", strip=True)
            if text:
                genres.append(text)
        # unique
        dedup = []
        seen = set()
        for g in genres:
            if g.lower() not in seen:
                seen.add(g.lower())
                dedup.append(g)
        return dedup

    @staticmethod
    def _extract_genres_vivutruyen(soup: BeautifulSoup, lines: Optional[list[str]] = None) -> list[str]:
        if lines:
            for idx, line in enumerate(lines):
                if line.lower() == "thể loại:":
                    direct = []
                    for value in lines[idx + 1 : idx + 6]:
                        low = value.lower()
                        if low in {"trạng thái:", "trạng thái", "theo dõi"}:
                            break
                        if MeokammapCrawler._is_vivutruyen_noise_line(value):
                            continue
                        direct.append(value)
                    if direct:
                        return direct

        genres = []
        for a in soup.select("a[href*='the-loai'], a[href*='genre']"):
            text = a.get_text(" ", strip=True)
            if not text:
                continue
            lower = text.lower()
            if lower in {"thể loại", "the loai", "trang chủ"}:
                continue
            genres.append(text)

        # If page has no dedicated links, fallback to menu items near "Thể Loại"
        if not genres:
            for item in soup.select("li, a, span"):
                text = item.get_text(" ", strip=True)
                if text and text.lower() not in {"thể loại", "the loai"} and len(text) <= 30:
                    if item.name == "a" and item.get("href", "").strip():
                        if "the-loai" in item.get("href", ""):
                            genres.append(text)

        dedup = []
        seen = set()
        for g in genres:
            k = g.lower().strip()
            if not k or k in seen:
                continue
            seen.add(k)
            dedup.append(g)
        return dedup

    def _extract_vivutruyen_summary(self, lines: list[str]) -> Optional[str]:
        status_idx = -1
        for idx, line in enumerate(lines):
            if line.lower() == "trạng thái:":
                status_idx = idx
                break
        if status_idx < 0:
            return None

        # Usually status value is immediately after label.
        start = min(status_idx + 2, len(lines))
        paragraphs = []
        stop_markers = [
            "theo dõi",
            "đăng nhập để theo dõi truyện này",
            "bắt đầu đọc",
            "danh sách chương",
            "bình luận",
            "truyện cùng thể loại",
            "website đang trong quá trình thử nghiệm",
        ]
        for line in lines[start:]:
            low = line.lower()
            if any(marker in low for marker in stop_markers):
                break
            if self._is_vivutruyen_noise_line(line):
                continue
            if len(line) < 20:
                continue
            paragraphs.append(line)
            if len(paragraphs) >= 8:
                break
        if paragraphs:
            return "\n\n".join(paragraphs)
        return None

    @staticmethod
    def _extract_vivutruyen_labeled_value(lines: list[str], label: str) -> Optional[str]:
        label_low = label.lower().strip()
        for idx, line in enumerate(lines):
            if line.lower().strip() != f"{label_low}:":
                continue
            if idx + 1 >= len(lines):
                return None
            value = lines[idx + 1].strip()
            if not value or value.endswith(":"):
                return None
            # Keep the field concise; avoid grabbing full paragraphs as metadata.
            if len(value) > 120:
                return None
            return value
        return None

    @staticmethod
    def _normalized_lines(text: str) -> list[str]:
        lines = []
        for line in text.splitlines():
            ln = re.sub(r"\s+", " ", line).strip()
            if ln:
                lines.append(ln)
        return lines

    @staticmethod
    def _is_vivutruyen_noise_line(line: str) -> bool:
        low = line.lower().strip()
        if not low:
            return True
        noise_keywords = [
            "đăng nhập",
            "đăng ký",
            "thoát",
            "thống kê",
            "đề cử",
            "xem nhiều",
            "mới cập nhật",
            "mới nhất",
            "tài khoản",
            "trang chủ",
            "thể loại",
            "prev next",
            "scroll up",
            "shopee",
            "lazada",
            "mở ứng dụng",
            "mở khóa toàn bộ chương truyện",
            "lưu ý: nội dung trên chỉ xuất hiện 1 lần trong ngày",
            "website đang trong quá trình thử nghiệm",
        ]
        return any(k in low for k in noise_keywords)

    def _clean_vivutruyen_chapter_content(self, text: str) -> str:
        kept = []
        for line in text.splitlines():
            ln = re.sub(r"\s+", " ", line).strip()
            if not ln:
                continue
            if self._is_vivutruyen_noise_line(ln):
                continue
            kept.append(ln)
        return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()

    @staticmethod
    def _extract_chapter_index(title: str) -> Optional[int]:
        m = re.search(r"(?:chapter|chap|ch|chuong)\s*[-.:]?\s*(\d+)", title, re.IGNORECASE)
        if not m:
            # e.g. "/chuong-12" style.
            m = re.search(r"chuong[-_/](\d+)", title, re.IGNORECASE)
        if not m:
            return None
        try:
            return int(m.group(1))
        except ValueError:
            return None

    @staticmethod
    def _clean_chapter_content(text: str) -> str:
        noisy_patterns = [
            r"^\s*Logo\b.*$",
            r"^\s*Năng Thơ\b.*$",
            r"^\s*Yêu Truyện\b.*$",
            r"^\s*Thể loại\s*$",
            r"^\s*Theo số chương\s*$",
            r"^\s*Tùy chỉnh\s*$",
            r"^\s*Phòng Chat\s*$",
            r"^\s*Chọn chương\s*$",
            r"^\s*Chương tiếp\s*$",
            r"^\s*Khám Phá\s*$",
            r"^\s*Thông Tin\s*$",
            r"^\s*Loading\.\.\.\s*$",
            r"^\s*Copyright\b.*$",
            r"^\s*Bạn có thể dùng phím.*$",
            r"^\s*Truy cập website đồng nghĩa.*$",
        ]
        cleaned_lines = []
        for line in text.splitlines():
            ln = re.sub(r"\s+", " ", line).strip()
            if not ln:
                continue
            if any(re.search(pat, ln, re.IGNORECASE) for pat in noisy_patterns):
                continue
            cleaned_lines.append(ln)
        cleaned = "\n".join(cleaned_lines).strip()
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned


def _write_json(data: Any, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _normalize_url_for_compare(url: str) -> str:
    clean = (url or "").strip()
    if not clean:
        return ""
    parsed = urlparse(clean)
    path = parsed.path.rstrip("/") or "/"
    return parsed._replace(path=path, fragment="").geturl()


def main() -> int:
    parser = argparse.ArgumentParser(description="Crawler for meokammap.com")
    parser.add_argument("--list", dest="list_url", help="Listing/category page URL")
    parser.add_argument("--story", dest="story_url", help="Story URL")
    parser.add_argument("--chapter", dest="chapter_url", help="Chapter URL")
    parser.add_argument(
        "--fetch-content",
        action="store_true",
        help="When used with --story, fetch full content for each chapter",
    )
    parser.add_argument(
        "--max-chapters",
        type=int,
        default=0,
        help="Limit number of chapters fetched (0 = all)",
    )
    parser.add_argument(
        "--output",
        default="output.json",
        help="Output JSON file path (default: output.json)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.6,
        help="Delay between chapter requests in seconds",
    )
    parser.add_argument(
        "--no-cloudscraper",
        action="store_true",
        help="Disable cloudscraper fallback",
    )

    args = parser.parse_args()

    if not any([args.list_url, args.story_url, args.chapter_url]):
        parser.error("Use one of: --list, --story, --chapter")

    client = HttpClient(use_cloudscraper=not args.no_cloudscraper)
    crawler = MeokammapCrawler(client=client, delay=args.delay)

    if args.list_url:
        links = crawler.discover_story_links(args.list_url)
        _write_json({"list_url": args.list_url, "story_links": links}, args.output)
        print(f"Saved {len(links)} story links to {args.output}")
        return 0

    if args.chapter_url:
        chapter = crawler.parse_chapter_content(args.chapter_url)
        _write_json(asdict(chapter), args.output)
        print(f"Saved chapter content to {args.output}")
        return 0

    story = crawler.parse_story(args.story_url)
    if args.fetch_content and story.chapters:
        total = len(story.chapters)
        chapters = story.chapters
        if args.max_chapters > 0:
            chapters = chapters[: args.max_chapters]
        for i, ch in enumerate(chapters, start=1):
            print(f"[{i}/{min(total, len(chapters))}] Fetching {ch.url}")
            full = crawler.parse_chapter_content(ch.url)
            ch.content = full.content
            if crawler.delay > 0:
                time.sleep(crawler.delay)
        story.chapters = chapters

    _write_json(asdict(story), args.output)
    print(f"Saved story data to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
