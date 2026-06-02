"""
========================================
  ScrollPack 插件开发快速指南
========================================

一分钟上手
----------
1. 复制 plugin_template/ 文件夹，改名
2. 改 plugin.json → name / id
3. 改 source.py → 三个方法即可

选择基类
--------
- 网站需要 JS 渲染 → 继承 BrowserSource（已内置浏览器管理）
- 纯 HTTP 就能抓 → 继承 LightNovelSource（轻量，无浏览器）

BrowserSource 已提供的方法（直接可用）
----------
  self._safe_goto(url)           # 打开页面，返回 True/False
  self._page.evaluate("js代码")   # 在页面里执行 JS
  self._page.eval_on_selector("选择器", "js代码")  # 取单个元素
  self._page.eval_on_selector_all("选择器", "js代码")  # 取所有元素
  self._human_delay(0.3, 1.5)    # 随机延迟（模拟人）
  self._domain                    # 当前网站域名

必须实现的三个方法
----------
  get_novel(url) → 返回 Novel 对象（标题/作者/封面）
  get_novel_catalog(novel) → 返回 Catalog 对象（分卷/章节列表）
  get_image(src) → 返回 bytes（单张图片）

漫画额外需实现
----------
  fetch_chapter_images(url) → 返回图片 URL 列表

常见问题
----------
Q: 章节列表是 JS 渲染的，怎么拿？
A: await self._safe_goto(详情页URL) → 等几秒 → self._page.eval_on_selector_all("a.xxx") 提取

Q: 图片懒加载，URL 在 data-src 属性里？
A: 先滚动触发加载，再取 el.src || el.getAttribute('data-src')

Q: 网站有 Cloudflare 验证？
A: BrowserSource 已处理 n.webdriver 伪装 + CDP 反调试，通常够用
"""

# 以下为完整可运行模板
import re
from typing import List
from urllib.parse import urljoin, urlparse

from plugins.base import BrowserSource
from utils.http_util import http_get_string, http_get_bytes
from models import Novel, Catalog, Volume, Chapter
from logger import logger


class MySource(BrowserSource):
    """你的源名称"""

    # 匹配网址的正则，capture group 1 = 漫画/小说 ID
    _URL_PAT = re.compile(r"你的域名\.com/路径/([\w-]+)")
    _domain: str = ""

    @property
    def name(self) -> str:
        return "我的源"

    @property
    def source_url(self) -> str:
        return self._domain or "https://你的域名.com"

    def support_url(self, url: str) -> bool:
        return bool(self._URL_PAT.search(url))

    # ── 1. 获取作品信息 ──────────────────────

    async def get_novel(self, url: str):
        self._domain = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        novel = Novel()
        novel.url = url
        novel.id = self._URL_PAT.search(url).group(1)
        novel.title = "待抓取"
        novel.author = "待抓取"

        if await self._safe_goto(url, wait_until="domcontentloaded"):
            await self._human_delay(1, 2)
            novel.title = await self._page.eval_on_selector(
                "h1", "el => el ? el.textContent.trim() : ''"
            )

        return novel

    # ── 2. 获取章节目录 ──────────────────────

    async def get_novel_catalog(self, novel):
        catalog = Catalog(novel)
        volume = Volume("默认", catalog)
        catalog.volumes.append(volume)

        # 示例：从页面提取链接
        if await self._safe_goto(novel.url, wait_until="domcontentloaded"):
            await self._human_delay(1, 2)
            chapters = await self._page.eval_on_selector_all(
                "a[href*='/chapter/']",
                "els => els.map(el => ({href: el.href, text: el.textContent.trim()}))"
            )
            for ch in chapters:
                if ch.get("text") and ch.get("href"):
                    volume.chapters.append(Chapter(ch["text"], ch["href"], volume))

        return catalog

    # ── 3. 获取章节内容（小说用） ────────────

    async def get_novel_chapter(self, chapter) -> str:
        return await http_get_string(chapter.chapter_url)

    # ── 4. 获取章节图片（漫画用） ────────────

    async def fetch_chapter_images(self, chapter_url: str) -> List[str]:
        if not await self._safe_goto(chapter_url, wait_until="domcontentloaded"):
            return []

        await self._human_delay(2, 3)

        # 滚动到底触发懒加载
        await self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await self._human_delay(1, 2)

        return await self._page.eval_on_selector_all(
            "img[src]",
            "els => els.map(el => el.src || '').filter(u => u)"
        )

    # ── 5. 下载图片 ──────────────────────────

    async def get_image(self, src: str) -> bytes:
        if not src.startswith("http"):
            src = urljoin(self._domain, src)
        return await http_get_bytes(src, headers={"Referer": self._domain})
