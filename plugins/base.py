import os
import random
import asyncio
from abc import ABC, abstractmethod
from typing import List

from logger import logger
from config import AppConfig


class LightNovelSource(ABC):
    """Base class for all content sources. Override abstract methods to create a plugin."""

    HTML_TEMPLATE = "<html xmlns='http://www.w3.org/1999/xhtml' lang='zh-CN'><body></body></html>"

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def source_url(self) -> str:
        pass

    @abstractmethod
    def support_url(self, url: str) -> bool:
        pass

    @abstractmethod
    async def get_novel(self, url: str):
        pass

    @abstractmethod
    async def get_novel_catalog(self, novel):
        pass

    @abstractmethod
    async def get_novel_chapter(self, chapter) -> str:
        pass

    @abstractmethod
    async def get_image(self, src: str) -> bytes:
        pass

    @property
    def is_manga(self) -> bool:
        return False

    async def fetch_chapter_images(self, chapter_url: str) -> List[str]:
        return []


class BrowserSource(LightNovelSource):
    """
    Base class for sources that require a real browser (Playwright).
    Provides shared browser management, proxy, and navigation logic.

    Subclass this if your site renders content with JavaScript.
    """

    is_manga = True
    _browser = None
    _playwright = None
    _page = None

    async def _ensure_browser(self):
        if self._browser is not None:
            try:
                if not self._browser.is_connected():
                    self._page = None
                    self._browser = None
            except Exception:
                self._page = None
                self._browser = None
        if self._browser is not None:
            return

        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
        if not browsers_path:
            project_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            browsers_path = os.path.join(project_dir, "playwright_browsers")
        if os.path.isdir(browsers_path):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browsers_path

        from playwright.async_api import async_playwright

        headless = AppConfig().headless
        args = ["--disable-blink-features=AutomationControlled"]
        if headless:
            args.append("--headless=new")

        self._playwright = await async_playwright().start()

        try:
            self._browser = await self._playwright.chromium.launch(
                headless=headless, channel="msedge", args=args,
            )
            logger.info("Using system Microsoft Edge")
        except Exception:
            try:
                self._browser = await self._playwright.chromium.launch(
                    headless=headless, channel="chrome", args=args,
                )
                logger.info("Using system Google Chrome")
            except Exception:
                import glob as _glob
                exe = None
                hits = _glob.glob(os.path.join(browsers_path, "chromium-*", "chrome-win*", "chrome.exe"))
                if hits:
                    exe = hits[0]
                self._browser = await self._playwright.chromium.launch(
                    headless=headless, executable_path=exe, args=args,
                )
                logger.info("Using bundled Chromium")

    def _build_proxy_settings(self):
        cfg = AppConfig()
        if not cfg.has_proxy:
            return None
        p = {"server": f"http://{cfg.proxy_host}:{cfg.proxy_port}"}
        if cfg.proxy_username and cfg.proxy_password:
            p["username"] = cfg.proxy_username
            p["password"] = cfg.proxy_password
        return p

    async def _ensure_page(self):
        await self._ensure_browser()
        if self._page is None or self._page.is_closed():
            if self._page is not None and not self._page.is_closed():
                await self._page.close()
            proxy_settings = self._build_proxy_settings()
            if proxy_settings:
                logger.info(f"Browser proxy: {proxy_settings['server']}")
            ctx = await self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                proxy=proxy_settings,
            )
            self._page = await ctx.new_page()
            self._page.set_default_timeout(60000)
            cdp = await self._page.context.new_cdp_session(self._page)
            await cdp.send("Debugger.setSkipAllPauses", {"skip": True})
            await self._page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            """)

    async def _safe_goto(self, url: str, wait_until: str = "domcontentloaded") -> bool:
        for attempt in range(3):
            try:
                await self._ensure_page()
                await self._page.goto(url, wait_until=wait_until, timeout=60000)
                return True
            except Exception as e:
                delay = (attempt + 1) * random.uniform(3, 8)
                logger.warning(f"Goto failed (attempt {attempt + 1}): {e}")
                if self._page:
                    try:
                        await self._page.close()
                    except Exception:
                        pass
                self._page = None
                self._browser = None
                if self._playwright:
                    try:
                        self._playwright.stop()
                    except Exception:
                        pass
                    self._playwright = None
                await asyncio.sleep(delay)
        return False

    async def _close_browser(self):
        if self._page:
            await self._page.close()
            self._page = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def _human_delay(self, min_s: float = 0.3, max_s: float = 1.5):
        await asyncio.sleep(random.uniform(min_s, max_s))
