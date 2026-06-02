import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from plugins.base import LightNovelSource
from models import Novel, Catalog, Volume, Chapter
from utils.http_util import http_get_string, http_get_bytes
from utils.html_util import HTMLUtil
from utils.url_util import URLUtil
from logger import logger
from scheduler import Scheduler
from config import AppConfig


class WenkuNovel(Novel):
    def __init__(self):
        super().__init__()
        self.catalog_url = ""


class WenkuNovelSource(LightNovelSource):
    _EXP1 = re.compile(r"wenku8\.net/book/(\d+)")
    _EXP2 = re.compile(r"wenku8\.net/novel/\d+/(\d+)/")
    DOMAIN = "https://www.wenku8.net"

    USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36")

    _scheduler = Scheduler(20, 60)

    @property
    def name(self):
        return "轻小说文库"

    @property
    def source_url(self):
        return "https://www.wenku8.net/login.php"

    def support_url(self, url):
        return bool(self._EXP1.search(url) or self._EXP2.search(url))

    def _get_id(self, url):
        m = self._EXP1.search(url)
        if m:
            return m.group(1)
        m = self._EXP2.search(url)
        if m:
            return m.group(1)
        raise ValueError(f"Unsupported url: {url}")

    async def get_novel(self, url):
        novel_id = self._get_id(url)
        novel = WenkuNovel()
        novel.url = f"{self.DOMAIN}/book/{novel_id}.htm"
        html = await self._http_get(novel.url, headers={"User-Agent": self.USER_AGENT})
        doc = BeautifulSoup(html, 'html.parser')
        try:
            novel.id = novel_id
            content = doc.select_one("#content")
            if not content:
                raise ValueError("Cannot parse novel page")
            title_el = content.select_one("table:nth-child(1) span b")
            novel.title = title_el.text.strip() if title_el else ""
            cover_el = content.select_one("#content table img")
            if cover_el:
                novel.cover_url = urljoin(self.DOMAIN, cover_el.get("src", ""))
            details = content.select("#content table:nth-child(1) tr:nth-child(2) td")
            if len(details) > 2:
                novel.status = details[2].text.strip().replace("文章状态：", "")
            if len(details) > 1:
                novel.author = details[1].text.strip().replace("小说作者：", "")
            tables = content.select("#content table")
            if len(tables) > 2:
                td = tables[2].select("td")
                if len(td) > 1:
                    span = td[1].select_one("span")
                    if span:
                        novel.tags = span.text.strip().replace("作品Tags：", "").split(" ")
                    spans = td[1].select("span")
                    if len(spans) > 1:
                        novel.description = spans[-1].text.strip()
            catalog_link = doc.select_one("legend + div > a")
            if catalog_link:
                href = catalog_link.get("href", "")
                if not href.startswith("http"):
                    href = urljoin(self.DOMAIN, href)
                novel.catalog_url = href
            return novel
        except Exception as e:
            logger.error(e)
            logger.info(html)
            raise

    async def get_novel_catalog(self, novel):
        url = novel.catalog_url
        prefix = URLUtil.resolve(url, "./")
        html = await self._http_get(url, headers={"User-Agent": self.USER_AGENT})
        doc = BeautifulSoup(html, 'html.parser')
        td_list = doc.select("table td")
        catalog = Catalog(novel)
        volume = None
        for td in td_list:
            cls = td.get("class", [])
            if "vcss" in cls:
                if volume:
                    catalog.volumes.append(volume)
                volume = Volume(td.text.strip(), catalog)
            elif "ccss" in cls:
                link = td.select_one("a")
                if not link or not volume:
                    continue
                href = link.get("href", "")
                chapter_url = f"{prefix}/{href}"
                chapter = Chapter(link.text.strip(), chapter_url, volume)
                if chapter.chapter_name == "插图":
                    volume.chapters.insert(0, chapter)
                else:
                    volume.chapters.append(chapter)
        if volume:
            catalog.volumes.append(volume)
        return catalog

    async def get_novel_chapter(self, chapter):
        import asyncio
        config = AppConfig()
        for attempt in range(1, config.max_retries + 1):
            try:
                return await self._get_novel_chapter(chapter)
            except Exception as e:
                if attempt < config.max_retries:
                    await asyncio.sleep(config.retry_delay_seconds)
                else:
                    raise

    async def _get_novel_chapter(self, chapter):
        url = chapter.chapter_url
        logger.info(f" ==> {chapter.volume.volume_name} {chapter.chapter_name} {url}")
        html = await self._http_get(url, headers={"User-Agent": self.USER_AGENT})
        if "Cloudflare" in html and "Ray ID" in html:
            raise RuntimeError("Cloudflare Error")
        doc = BeautifulSoup(html, 'html.parser')
        content = doc.select_one("#content")
        if not content:
            logger.info(f"GET {url} ERROR")
            logger.info(html)
            raise RuntimeError("运行出错，请提交Issues并上传日志文件")
        logger.info(f"GET {url} OK")
        HTMLUtil.remove_elements(content.select("#contentdp"))
        HTMLUtil.remove_elements(content.select("br"))
        return self._wrap_document(content)

    def _wrap_document(self, content):
        from bs4 import NavigableString, BeautifulSoup as BS
        doc = BS(LightNovelSource.HTML_TEMPLATE, 'html.parser')
        for node in list(content.children):
            if isinstance(node, NavigableString):
                text = str(node).strip()
                if not text:
                    continue
                p = doc.new_tag("p")
                p.string = text
                doc.body.append(p)
            else:
                parsed = BS(str(node), 'html.parser')
                pb = parsed.body or parsed
                for child in list(pb.children):
                    doc.body.append(child)
        for link in doc.find_all("a"):
            HTMLUtil.unwrap(link)
        return str(doc)

    async def get_image(self, src):
        async def _task(c):
            return await http_get_bytes(src, headers={"User-Agent": self.USER_AGENT})
        return await self._scheduler.run(_task)

    async def _http_get(self, url, headers=None):
        import asyncio as _asyncio
        while True:
            async def _task(c):
                html = await http_get_string(url, headers=headers, encoding='gbk')
                if "rate limited" in html:
                    logger.info(f"GET {url} Reach rate limit")
                    c.pause()
                    await _asyncio.sleep(10)
                    c.resume()
                    raise RuntimeError("rate limited, retry")
                return html
            try:
                return await self._scheduler.run(_task)
            except RuntimeError:
                continue
