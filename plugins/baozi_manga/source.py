import re
import asyncio
import random
from typing import List
from urllib.parse import urljoin, urlparse

from plugins.base import BrowserSource
from utils.http_util import http_get_bytes
from models import Novel, Catalog, Volume, Chapter
from logger import logger
from config import AppConfig


class BaoziMangaSource(BrowserSource):
    _URL_PAT = re.compile(r"baozimh\.\w+/manga/([a-zA-Z0-9_-]+)")

    _browser: Browser | None = None
    _playwright = None
    _page: Page | None = None
    _domain: str = ""
    _slug: str = ""

    @property
    def name(self) -> str:
        return "\u5305\u5b50\u6f2b\u753b"

    @property
    def source_url(self) -> str:
        return self._domain or "https://baozimh.org"

    def support_url(self, url: str) -> bool:
        return bool(self._URL_PAT.search(url))

    def _get_slug(self, url: str) -> str:
        m = self._URL_PAT.search(url)
        if not m:
            raise ValueError(f"Unsupported url: {url}")
        return m.group(1)

    def _get_domain(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    async def get_novel(self, url: str):
        self._domain = self._get_domain(url)
        slug = self._get_slug(url)
        self._slug = slug
        novel = Novel()
        novel.url = url
        novel.id = slug

        if not await self._safe_goto(f"{self._domain}/manga/{slug}", wait_until="domcontentloaded"):
            raise RuntimeError(f"Failed to load novel page: {url}")

        await self._human_delay(1, 2)

        novel.title = await self._page.eval_on_selector(
            "h1", "el => el ? el.textContent.trim() : ''"
        )
        if not novel.title:
            t = await self._page.evaluate("document.title || ''")
            parts = t.split("-")
            novel.title = parts[0].strip() if parts else slug

        novel.author = await self._page.eval_on_selector(
            'a[href*="/manga-author/"]',
            'el => el ? el.textContent.trim().replace(/,/g, "").trim() : ""',
        )
        if not novel.author:
            novel.author = "\u672a\u77e5"

        novel.cover_url = None
        cover = await self._page.eval_on_selector(
            "#MangaCard img[alt]",
            'el => el ? (el.src || "") : ""',
        )
        if cover:
            novel.cover_url = urljoin(self._domain, cover)

        novel.tags = await self._page.eval_on_selector_all(
            'a[href*="/manga-tag/"]',
            "els => els.map(el => el.textContent.trim().replace('#', '')).filter(t => t)",
        )

        novel.publisher = "\u5305\u5b50\u6f2b\u753b"
        # Try to get status
        status_el = await self._page.eval_on_selector(
            "h1 span", "el => el ? el.textContent.trim() : ''"
        )
        novel.status = status_el or ""
        novel.description = novel.title

        return novel

    async def get_novel_catalog(self, novel):
        slug = novel.id
        catalog = Catalog(novel)
        catalog = await self._crawl_chapters(slug, catalog)
        return catalog

    async def _crawl_chapters(self, slug: str, catalog: Catalog) -> Catalog:
        await self._ensure_page()

        if not await self._safe_goto(
            f"{self._domain}/manga/{slug}", wait_until="domcontentloaded"
        ):
            logger.warning("Failed to load detail page")
            return catalog

        # Wait for chapter list to render (Alpine.js)
        try:
            await self._page.wait_for_selector("#sortchapters a[href]", timeout=20000)
        except Exception:
            pass
        await asyncio.sleep(2)

        # Also click "查看所有章節" if exists
        try:
            await self._page.click("#morechap a")
            await asyncio.sleep(2)
        except Exception:
            pass

        # Extract chapter links
        raw = await self._page.eval_on_selector_all(
            "#sortchapters a[href*='/manga/']",
            """els => els.map(el => {
                const text = el.textContent.replace(/\d{1,2}月\d{1,2}日/g, '').trim();
                return {href: el.href, text: text};
            })""",
        )

        if not raw:
            raw = await self._page.eval_on_selector_all(
                "a[href*='/manga/']",
                """els => els.filter(el => {
                    const h = el.getAttribute('href') || '';
                    return h.includes(slug) && h.split('/').length >= 4;
                }).map(el => ({
                    href: el.href,
                    text: el.textContent.trim()
                }))"""
            )

        if raw:
            volume = Volume("\u9ed8\u8ba4", catalog)
            for link in raw:
                name = link.get("text") or ""
                href = link.get("href", "")
                if name and href:
                    volume.chapters.append(Chapter(name, href, volume))
            if volume.chapters:
                catalog.volumes.append(volume)
                logger.info(f"Extracted {len(volume.chapters)} chapters")
            return catalog

        return catalog

    async def get_novel_chapter(self, chapter) -> str:
        return ""

    async def get_image(self, src: str) -> bytes:
        if not src.startswith("http"):
            src = urljoin(self._domain, src)
        for _ in range(5):
            try:
                data = await http_get_bytes(
                    src,
                    headers={
                        "Referer": self._domain,
                        "Accept": "image/webp,image/*,*/*;q=0.8",
                    },
                    timeout=20,
                    max_attempts=1,
                )
                if data:
                    return data
            except Exception:
                pass
            await asyncio.sleep(1.5)
        logger.warning(f"Failed to download: {src[:100]}")
        return b""

    async def fetch_chapter_images(self, chapter_url: str) -> List[str]:
        logger.info(f"Loading chapter: {chapter_url}")
        if not await self._safe_goto(chapter_url, wait_until="domcontentloaded"):
            return []

        await asyncio.sleep(2)

        # Wait for spinner to disappear and images to appear
        try:
            await self._page.wait_for_function(
                """() => {
                    const loadimg = document.getElementById('loadimg');
                    if (loadimg && loadimg.style.display !== 'none') return false;
                    const imgs = document.querySelectorAll('#chapcontent img[src]');
                    return imgs.length > 0;
                }""",
                timeout=30000,
            )
        except Exception:
            pass
        await asyncio.sleep(1)

        prev = 0
        for _ in range(6):
            await self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)
            cur = await self._page.evaluate(
                "document.querySelectorAll('#chapcontent img[src]').length"
            )
            if cur == prev and cur > 0:
                break
            prev = cur

        img_urls = await self._page.eval_on_selector_all(
            "#chapcontent img",
            "els => els.map(el => el.src || el.getAttribute('data-src') || '').filter(u => u && !u.startsWith('data:'))",
        )

        if not img_urls:
            # Try all images on page
            img_urls = await self._page.evaluate("""() => {
                const imgs = document.querySelectorAll('img[src]');
                const urls = [];
                for (const img of imgs) {
                    const u = img.src;
                    if (u && !u.startsWith('data:') && !u.includes('loading') && !u.includes('logo') && !u.includes('ad')) {
                        urls.push(u);
                    }
                }
                return urls;
            }""")

        if img_urls:
            logger.info(f"  Found {len(img_urls)} images, sample: {img_urls[0][:100] if img_urls else 'none'}")
            return img_urls
