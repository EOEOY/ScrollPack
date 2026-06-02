import re
import base64
from bs4 import BeautifulSoup
from urllib.parse import urljoin

from plugins.base import LightNovelSource
from utils.http_util import http_get_string, http_get_bytes
from models import Novel, Catalog, Volume, Chapter
from utils.html_util import HTMLUtil
from logger import logger
from scheduler import Scheduler
from plugins.bili_novel.font_secret import FONT_SECRET_MAP
from plugins.bili_novel.secret import get_secret_map, DOMAIN
from plugins.bili_novel.chapterlog import BiliChapterLogResolver
from config import AppConfig


class BiliNovelSource(LightNovelSource):
    _EXP = re.compile(r"(?:linovelib|bilinovel)\.com/(?:novel|download)/(\d+)")
    DOMAIN = DOMAIN

    USER_AGENT = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
    COOKIE = "night=0"

    _secret_map = {}
    _scheduler = Scheduler(15, 60)
    _image_scheduler = Scheduler(10, 1)
    _init_done = False

    @property
    def name(self):
        return "哔哩轻小说"

    @property
    def source_url(self):
        return DOMAIN

    @classmethod
    async def _ensure_init(cls):
        if not cls._init_done:
            try:
                cls._secret_map = await get_secret_map()
            except Exception as e:
                logger.warning(f"Failed to init secret map: {e}")
            cls._init_done = True

    def support_url(self, url):
        return bool(self._EXP.search(url))

    def _get_id(self, url):
        m = self._EXP.search(url)
        if not m:
            raise ValueError(f"Unsupported url: {url}")
        return m.group(1)

    async def get_novel(self, url):
        novel_id = self._get_id(url)
        novel = Novel()
        novel.url = url
        headers = {"Accept-Language": "zh-CN,zh;q=0.9"}
        html = await http_get_string(f"{DOMAIN}/novel/{novel_id}.html", headers=headers)
        doc = BeautifulSoup(html, 'html.parser')
        try:
            novel.id = novel_id
            title_el = doc.select_one(".book-title")
            novel.title = title_el.text.strip() if title_el else ""
            backup_el = doc.select_one(".backupname .bkname-body.gray")
            if backup_el:
                novel.alias = backup_el.text.strip()
            cover_el = doc.select_one(".book-layout img")
            if cover_el:
                src = cover_el.get("src") or cover_el.get("data-src", "")
                novel.cover_url = urljoin(DOMAIN, src) if src else None
            novel.tags = [e.text.strip() for e in doc.select(".book-cell .book-meta span em")]
            pub_el = doc.select_one(".tag-small.orange")
            if pub_el:
                novel.publisher = pub_el.text.strip()
            status_el = doc.select(".book-cell .book-meta+.book-meta")
            if status_el:
                nodes = list(status_el[-1].children)
                if nodes:
                    novel.status = str(nodes[-1]).strip()
            author_el = doc.select_one(".book-rand-a span")
            novel.author = author_el.text.strip() if author_el else ""
            desc_el = doc.select_one("#bookSummary content")
            novel.description = desc_el.text.strip() if desc_el else ""
            return novel
        except Exception as e:
            logger.error(e)
            logger.info(html)
            raise

    async def get_novel_catalog(self, novel):
        url = f"{DOMAIN}/novel/{novel.id}/catalog"
        headers = {"Accept-Language": "zh-CN,zh;q=0.9"}
        html = await http_get_string(url, headers=headers)
        doc = BeautifulSoup(html, 'html.parser')
        catalog = Catalog(novel)
        VolumeSource._replace_image_src(doc.body if doc.body else doc)
        volume = None
        if not doc.select_one(".chapter-bar"):
            volume = Volume("", catalog)
        lis = doc.select(".volume-chapters>li")
        if not lis:
            logger.info(f"GET {url}")
            logger.info(html)
            raise ValueError("目录获取为空")
        for li in lis:
            classes = li.get("class", [])
            if "chapter-bar" in classes:
                if volume:
                    catalog.volumes.append(volume)
                volume = Volume(li.text.strip(), catalog)
            elif "volume-cover" in classes:
                if volume:
                    a_tag = li.select_one("a")
                    img_tag = a_tag.select_one("img") if a_tag else None
                    if img_tag:
                        volume.cover = urljoin(DOMAIN, img_tag.get("src") or img_tag.get("data-src", ""))
            elif "jsChapter" in classes:
                link = li.select_one("a")
                if link and volume:
                    name = link.text.strip()
                    href = link.get("href", "")
                    if not href or "javascript" in href:
                        href = None
                    else:
                        href = urljoin(DOMAIN, href)
                    chapter = Chapter(name, href, volume)
                    volume.chapters.append(chapter)
        if volume:
            catalog.volumes.append(volume)
        return catalog

    async def get_novel_chapter(self, chapter):
        await self._ensure_init()
        from bs4 import BeautifulSoup as BS
        doc = BS(LightNovelSource.HTML_TEMPLATE, 'html.parser')
        if not chapter.chapter_url:
            chapter.chapter_url = await self._get_chapter_url(chapter)
        if not chapter.chapter_url:
            raise ValueError("Empty chapter url")
        logger.info(f" ==> {chapter.volume.volume_name} {chapter.chapter_name} {chapter.chapter_url}")
        next_url = chapter.chapter_url
        chapter_log_resolver = BiliChapterLogResolver(
            load_script=lambda u: self._http_get_string(u),
            log_info=lambda m: logger.info(m),
        )
        while next_url:
            page = await self._get_chapter_page(next_url, chapter_log_resolver)
            if page.title and page.title != chapter.chapter_name and "〇" not in page.title:
                chapter.chapter_name = page.title
            for content in page.contents:
                doc.body.append(content) if doc.body else None
            next_url = page.next_page_url
        if doc.body:
            HTMLUtil.remove_line_break(doc.body)
            VolumeSource._replace_image_src(doc.body)
        html_str = str(doc)
        html_str = VolumeSource._apply_secret_maps(html_str, self._secret_map, FONT_SECRET_MAP)
        return html_str

    async def _get_chapter_url(self, chapter):
        catalog = chapter.volume.catalog
        next_ch = VolumeSource._get_next_chapter(catalog, chapter)
        if next_ch and next_ch.chapter_url:
            page = await self._get_chapter_page(next_ch.chapter_url)
            if page.prev_chapter_url:
                return page.prev_chapter_url
        prev_ch = VolumeSource._get_prev_chapter(catalog, chapter)
        if prev_ch and prev_ch.chapter_url:
            page = await self._get_chapter_page(prev_ch.chapter_url)
            for _ in range(20):
                if not page.next_page_url:
                    return page.next_chapter_url
                page = await self._get_chapter_page(page.next_page_url)
        return None

    async def _get_chapter_page(self, url, chapter_log_resolver=None):
        html = await self._http_get_string(url)
        doc = BeautifulSoup(html, 'html.parser')
        title = None
        if "_" not in url:
            title_el = doc.select_one("#atitle")
            if title_el:
                title = title_el.text.strip()
        content = (doc.select_one("#acontent") or doc.select_one(".bcontent"))
        if not content:
            content = doc.select_one("body") or doc
            logger.info(f"GET {url} use body fallback")
        else:
            logger.info(f"GET {url} OK")

        prev_page = None
        next_page = None
        prev_chapter = None
        next_chapter = None

        m = re.search(r"url_previous:'(.*?)',url_next:'(.*?)'", doc.decode() if hasattr(doc, 'decode') else str(doc))
        prev_url = m.group(1) if m else None
        next_url = m.group(2) if m else None
        footlinks = doc.select("#footlink a")
        prev_a = footlinks[0] if len(footlinks) > 0 else None
        next_a = footlinks[-1] if len(footlinks) > 1 else None
        if prev_a and (prev_a.text.strip() in ("上一页", "上一頁")) and prev_url:
            prev_page = urljoin(DOMAIN, prev_url)
        elif prev_a and prev_url:
            prev_chapter = urljoin(DOMAIN, prev_url)
        if next_a and (next_a.text.strip() in ("下一页", "下一頁")) and next_url:
            next_page = urljoin(DOMAIN, next_url)
        elif next_a and next_url:
            next_chapter = urljoin(DOMAIN, next_url)

        HTMLUtil.remove_elements(content.select("div"))
        HTMLUtil.remove_elements(content.select("ins"))
        HTMLUtil.remove_elements(content.select("figure"))
        HTMLUtil.remove_elements(content.select("fig"))
        HTMLUtil.remove_elements(content.select("br"))
        HTMLUtil.remove_elements(content.select("script"))
        HTMLUtil.remove_elements(content.select(".tp"))
        HTMLUtil.remove_elements(content.select(".bd"))
        HTMLUtil.remove_by_pattern(content, r"[a-z]\d{4}")

        if chapter_log_resolver:
            params = await chapter_log_resolver.get_shuffle_params(html)
            if params:
                VolumeSource._shuffle(content, params)

        return _ChapterPage(
            title=title,
            contents=list(content.children) if hasattr(content, 'children') else [],
            prev_page_url=prev_page,
            next_page_url=next_page,
            prev_chapter_url=prev_chapter,
            next_chapter_url=next_chapter,
        )

    async def get_image(self, src):
        if src.startswith("data:image"):
            return base64.b64decode(src.split(",", 1)[1])
        if not src.startswith("http"):
            src = urljoin(DOMAIN, src)
        src = src.replace("https://https://", "https://")
        src = src.replace("\ud835\ude23", "b")
        async def _task(_c):
            return await http_get_bytes(src, headers={
                "Referer": DOMAIN,
                "User-Agent": self.USER_AGENT,
                "Cache-Control": "public",
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Cookie": self.COOKIE,
            })
        return await self._image_scheduler.run(_task)

    async def _http_get_string(self, url):
        import asyncio as _asyncio
        while True:
            async def _task(c):
                html = await http_get_string(url, headers={
                    "User-Agent": self.USER_AGENT,
                    "Accept": "*/*",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                    "Cookie": self.COOKIE,
                })
                if "Cloudflare to restrict access" in html or "503 Service Temporarily Unavailable" in html:
                    logger.info(f"Cloudflare/503 detected, pausing...")
                    c.pause()
                    await _asyncio.sleep(10)
                    c.resume()
                    raise RuntimeError("cloudflare_block")
                return html
            try:
                return await self._scheduler.run(_task)
            except RuntimeError as e:
                if str(e) == "cloudflare_block":
                    await _asyncio.sleep(2)
                    continue
                raise


class VolumeSource:
    """Static helpers shared across sources for volume/chapter operations."""

    @staticmethod
    def _get_prev_chapter(catalog, chapter):
        all_chapters = []
        for v in catalog.volumes:
            all_chapters.extend(v.chapters)
        try:
            idx = all_chapters.index(chapter)
        except ValueError:
            return None
        return all_chapters[idx - 1] if idx > 0 else None

    @staticmethod
    def _get_next_chapter(catalog, chapter):
        all_chapters = []
        for v in catalog.volumes:
            all_chapters.extend(v.chapters)
        try:
            idx = all_chapters.index(chapter)
        except ValueError:
            return None
        return all_chapters[idx + 1] if idx < len(all_chapters) - 1 else None

    @staticmethod
    def _replace_image_src(element):
        if element is None:
            return
        for img in element.find_all('img'):
            src = img.get("data-src") or img.get("src", "")
            if src:
                if "<" in src:
                    img.decompose()
                    continue
                if src.startswith("//"):
                    src = "https:" + src
                img["src"] = src
            for attr in list(img.attrs.keys()):
                if attr not in ("alt", "class", "dir", "height", "id", "ismap",
                                "lang", "longdesc", "style", "title", "usemap",
                                "width", "src", "xml:lang"):
                    del img[attr]
            img["alt"] = img.get("alt", "")

    @staticmethod
    def _shuffle(content, params):
        from bs4 import Tag, NavigableString
        paragraphs = [p for p in content.find_all('p') if p.get_text(strip=True)]
        if not paragraphs:
            return
        fixed_len = params["fixedLength"]
        a = params["a"]
        c = params["c"]
        mod = params["mod"]
        seed = params["seed"]
        fixed = list(range(fixed_len))
        shuffled = list(range(fixed_len, len(paragraphs)))
        for i in range(len(shuffled) - 1, 0, -1):
            seed = (seed * a + c) % mod
            j = int((seed / mod) * (i + 1))
            shuffled[i], shuffled[j] = shuffled[j], shuffled[i]
        indices = fixed + shuffled
        mapped = [None] * len(paragraphs)
        for i, idx in enumerate(indices):
            if idx < len(paragraphs):
                mapped[idx] = paragraphs[i]
        children = list(content.children)
        replaced = 0
        for child in children:
            if hasattr(child, 'name') and child.name == 'p' and child.get_text(strip=True):
                if replaced < len(mapped) and mapped[replaced]:
                    child.replace_with(mapped[replaced])
                replaced += 1


    @staticmethod
    def _apply_secret_maps(html_str, secret_map, font_map):
        result = html_str
        if font_map:
            pua_set = set(c for c in result if '\ue000' <= c <= '\uf8ff')
            if pua_set:
                for ch in pua_set:
                    if ch in font_map:
                        result = result.replace(ch, font_map[ch])
        if secret_map:
            for k, v in secret_map.items():
                if k in result:
                    result = result.replace(k, v)
        return result


class _ChapterPage:
    def __init__(self, contents, title=None, prev_page_url=None,
                 next_page_url=None, prev_chapter_url=None, next_chapter_url=None):
        self.title = title
        self.contents = contents
        self.prev_page_url = prev_page_url
        self.next_page_url = next_page_url
        self.prev_chapter_url = prev_chapter_url
        self.next_chapter_url = next_chapter_url
