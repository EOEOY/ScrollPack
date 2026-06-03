import os
import re
import asyncio
import traceback
import zipfile
from typing import Callable, List

from models import Novel, Catalog, Volume, Chapter
from pack_argument import PackArgument
from config import AppConfig
from epub.packer import EpubPacker
from epub.constants import JPEG
from epub.cover_detector import LightNovelCoverDetector, UnsupportedImageException
from plugins import load_all_sources
from utils.html_util import HTMLUtil
from utils.volume_util import VolumeUtil
from utils.sequence import Sequence
from logger import logger

ProgressCallback = Callable[[str, str], None]


class ChapterResult:
    def __init__(self, chapter_name, document=None, error=None):
        self.chapter_name = chapter_name
        self.document = document
        self.error = error

    @property
    def is_success(self):
        return self.document is not None


class NovelPacker:
    sources = []

    @classmethod
    def _ensure_sources(cls):
        cls.sources = load_all_sources()

    def __init__(self, source, url):
        self.light_novel_source = source
        self.url = url
        self._image_sequence = Sequence()
        self._chapter_sequence = Sequence()
        self.novel = None
        self.catalog = None
        self.on_progress: ProgressCallback | None = None

    @property
    def _is_manga(self):
        return getattr(self.light_novel_source, "is_manga", False)

    @classmethod
    def from_url(cls, url):
        cls._ensure_sources()
        has_sources = len(cls.sources) > 0
        for source in cls.sources:
            if source.support_url(url):
                return cls(source, url)
        if not has_sources:
            raise ValueError("没有安装任何插件，请先在设置中下载插件")
        names = ", ".join(s.name for s in cls.sources[:4])
        raise ValueError(
            f"未识别网址，请确认链接包含作品ID\n"
            f"例如: https://www.bilinovel.com/novel/12345.html\n"
            f"已装插件: {names}"
        )

    async def init(self, novel_callback=None, catalog_callback=None):
        self.novel = await self.get_novel()
        if novel_callback:
            novel_callback(self.novel)
        self.catalog = await self.get_catalog()
        if catalog_callback:
            catalog_callback(self.catalog)
        return self.novel

    async def get_novel(self):
        self.novel = await self.light_novel_source.get_novel(self.url)
        return self.novel

    async def get_catalog(self):
        self.catalog = await self.light_novel_source.get_novel_catalog(self.novel)
        return self.catalog

    async def pack(self, arg: PackArgument) -> List[str]:
        reports = []
        config = AppConfig()
        if not arg.combine_volume:
            for volume in arg.pack_volumes:
                if self.on_progress:
                    self.on_progress("volume_start", volume.volume_name)
                self._image_sequence.reset()
                self._chapter_sequence.reset()
                if self._is_manga:
                    if arg.output_format == "cbz":
                        result = await self._pack_manga_cbz(volume)
                    else:
                        result = await self._pack_manga_volume(volume)
                else:
                    result = await self._pack_volume(volume, arg.add_chapter_title, config)
                reports.append(result)
                if self.on_progress:
                    self.on_progress("volume_done", volume.volume_name)
        else:
            if self._is_manga:
                raise ValueError("漫画不支持合并分卷")
            title = self._sanitize_filename(self.novel.title)
            path = os.path.join(AppConfig().output_dir or ".", title, f"{title}.epub")
            result = await self._combine_volume(path, arg, config)
            reports.append(result)
        if self.on_progress:
            self.on_progress("pack_done", "全部打包完成")
        return reports

    async def _combine_volume(self, path, arg, config):
        packer = EpubPacker(path)
        packer.doc_title = self.novel.title
        packer.creator = self.novel.author
        packer.source = self.novel.url
        packer.publisher = self.novel.publisher
        packer.subjects = self.novel.tags or []
        packer.description = self.novel.description

        cover_data = b""
        if self.novel.cover_url:
            cover_data = await self._get_single_image(self.novel.cover_url)
        if cover_data:
            cover_name = f"images/{self._image_sequence.next:06d}.jpg"
            packer.set_cover_image(cover_name, cover_data)

        if arg.add_chapter_title:
            packer.add_stylesheet()

        for volume in arg.pack_volumes:
            if self.on_progress:
                self.on_progress("volume_start", volume.volume_name)
            results = await self._resolve_chapters_with_retry(
                volume.chapters, packer, arg.add_chapter_title, config
            )
            first_href = None
            for i, result in enumerate(results):
                chapter = volume.chapters[i]
                if result.is_success:
                    doc_str = result.document
                    doc_str = self._add_title_to_html(doc_str, chapter.chapter_name)
                    doc_str = self._close_tag(doc_str)
                    doc_str = self._append_xml_declare(doc_str)
                    name = f"chapter{self._chapter_sequence.next:06d}.xhtml"
                    full_name = f"OEBPS/{name}"
                    packer.add_chapter(full_name, chapter.chapter_name, doc_str)
                    if i == 0:
                        first_href = name
            packer.add_nav_point(volume.volume_name, first_href)
            if self.on_progress:
                self.on_progress("volume_done", volume.volume_name)
        packer.pack()
        if self.on_progress:
            self.on_progress("info", f"打包完成: {packer.absolute_path}")
        return packer.absolute_path

    async def _resolve_chapter(self, chapter, packer, add_chapter_title, detector=None):
        doc_str = await self.light_novel_source.get_novel_chapter(chapter)
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(doc_str, 'html.parser')
        await self._resolve_images(soup, packer, detector)
        if add_chapter_title:
            head = soup.head or soup.new_tag("head")
            link = soup.new_tag("link", rel="stylesheet", type="text/css", href="styles/style.css")
            head.append(link)
            if not soup.head:
                soup.html.insert(0, head) if soup.html else None
            body = soup.body
            if body:
                title_div = soup.new_tag("div", **{"class": "chapter-title"})
                title_div.string = chapter.chapter_name
                body.insert(0, title_div)
        return str(soup)

    async def _resolve_chapters_with_retry(self, chapters, packer, add_chapter_title, config, detector=None):
        results = []
        for chapter in chapters:
            if AppConfig().cancelled:
                if self.on_progress:
                    self.on_progress("chapter_fail", f"{chapter.chapter_name} 已取消")
                results.append(ChapterResult(chapter.chapter_name, error="cancelled"))
                continue
            if self.on_progress:
                self.on_progress("chapter_start", chapter.chapter_name)
            result = None
            for attempt in range(1, config.max_retries + 1):
                try:
                    doc_str = await self._resolve_chapter(chapter, packer, add_chapter_title, detector)
                    result = ChapterResult(chapter.chapter_name, document=doc_str)
                    if self.on_progress:
                        self.on_progress("chapter_done", chapter.chapter_name)
                    break
                except Exception as e:
                    if attempt < config.max_retries:
                        msg = f"{chapter.chapter_name} (第{attempt}次重试) - {e}"
                        if self.on_progress:
                            self.on_progress("chapter_retry", msg)
                        logger.warning(msg)
                        await asyncio.sleep(config.retry_delay_seconds)
                    else:
                        tb = traceback.format_exc()
                        msg = f"{chapter.chapter_name} 失败: {e}"
                        logger.error(f"{msg}\n{tb}")
                        if self.on_progress:
                            self.on_progress("chapter_fail", msg)
                        result = ChapterResult(chapter.chapter_name, error=str(e))
            results.append(result)
        return results

    async def _get_single_image(self, src):
        try:
            return await self.light_novel_source.get_image(src)
        except Exception:
            return b""

    async def _pack_volume(self, volume, add_chapter_title, config):
        if self.on_progress:
            self.on_progress("volume_start", volume.volume_name)
        packer = EpubPacker(self._get_epub_name(volume))
        packer.doc_title = f"{volume.catalog.novel.title} {volume.volume_name}"
        if volume.volume_name.startswith(volume.catalog.novel.title):
            packer.doc_title = volume.volume_name
        packer.creator = volume.catalog.novel.author
        packer.source = self.novel.url
        packer.publisher = self.novel.publisher
        packer.subjects = self.novel.tags or []
        packer.description = self.novel.description
        packer.calibre_series_index = VolumeUtil.get_series_index(volume.volume_name)
        if packer.calibre_series_index is not None:
            packer.calibre_series = volume.catalog.novel.title

        detector = LightNovelCoverDetector()
        if add_chapter_title:
            packer.add_stylesheet()

        results = await self._resolve_chapters_with_retry(
            volume.chapters, packer, add_chapter_title, config, detector
        )
        for i, result in enumerate(results):
            chapter = volume.chapters[i]
            if result.is_success:
                doc_str = result.document
                doc_str = self._add_title_to_html(doc_str, chapter.chapter_name)
                doc_str = self._close_tag(doc_str)
                doc_str = self._append_xml_declare(doc_str)
                name = f"chapter{self._chapter_sequence.next:06d}.xhtml"
                packer.add_chapter(f"OEBPS/{name}", chapter.chapter_name, doc_str)
        await self._resolve_cover(volume, packer, detector)
        packer.pack()
        if self.on_progress:
            self.on_progress("volume_done", f"{volume.volume_name} -> {packer.absolute_path}")
        return packer.absolute_path

    async def _resolve_images(self, soup, packer, detector):
        imgs = soup.find_all("img")
        for img in imgs:
            src = img.get("src", "")
            if not src:
                continue
            data = await self._get_single_image(src)
            if not data:
                logger.warning(f"图片下载失败: {src}")
                continue
            name = f"{self._image_sequence.next:06d}.jpg"
            rel_src = f"images/{name}"
            packer.add_image(f"OEBPS/{rel_src}", data)
            img["src"] = rel_src
            if detector:
                try:
                    detector.add(f"OEBPS/{rel_src}", data)
                except UnsupportedImageException as e:
                    logger.warning(f"封面检测失败: {src} - {e.message}")
        if soup.body:
            HTMLUtil.wrap_duokan_image(soup.body)

    async def _resolve_cover(self, volume, packer, detector):
        if volume.cover:
            cover_data = await self._get_single_image(volume.cover)
            if cover_data:
                name = f"images/{self._image_sequence.next:06d}.jpg"
                packer.set_cover_image(name, cover_data)
        else:
            detected = detector.detect_cover()
            if detected:
                cover_name, cover_data = detected
                if cover_name and cover_data:
                    packer.set_cover_image(cover_name.replace("OEBPS/", ""), cover_data)
                elif cover_name:
                    packer.cover = cover_name.replace("OEBPS/", "")

    def _get_epub_name(self, volume):
        out_dir = AppConfig().output_dir or "."
        title = self._sanitize_filename(volume.catalog.novel.title)
        vol_name = self._sanitize_filename(volume.volume_name)
        if not vol_name:
            return os.path.join(out_dir, title, f"{title}.epub")
        if vol_name.startswith(title):
            return os.path.join(out_dir, title, f"{vol_name}.epub")
        return os.path.join(out_dir, title, f"{title} {vol_name}.epub")

    @staticmethod
    def _sanitize_filename(name):
        for ch in (':', '*', '?', '"', '\\', '/', '<', '>', '|', '\0', '　'):
            name = name.replace(ch, ' ')
        name = name.lstrip('.').rstrip('.')
        name = re.sub(r'\s+', ' ', name)
        return name.strip()

    @staticmethod
    def _add_title_to_html(html_str, title):
        return html_str.replace('<head>', f'<head><title>{title}</title>', 1)

    @staticmethod
    def _close_tag(html_str):
        return re.sub(r'(<(?:img|link)[^>]*?)>', r'\1/>', html_str)

    @staticmethod
    def _append_xml_declare(html_str):
        return '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n' + html_str

    async def close(self):
        if hasattr(self.light_novel_source, '_close_browser'):
            await self.light_novel_source._close_browser()

    async def _pack_manga_cbz(self, volume: Volume) -> str:
        out_dir = AppConfig().output_dir or "."
        title = self._sanitize_filename(self.novel.title)
        vol_name = self._sanitize_filename(volume.volume_name)
        if vol_name and title in vol_name:
            cbz_name = vol_name
        elif vol_name:
            cbz_name = f"{title} {vol_name}"
        else:
            cbz_name = title
        cbz_path = os.path.join(out_dir, title, f"{cbz_name}.cbz")
        os.makedirs(os.path.dirname(cbz_path) or ".", exist_ok=True)

        img_index = 0
        failed_images = []
        page_count = 0

        if os.path.exists(cbz_path):
            os.remove(cbz_path)

        cover_data = None
        if volume.cover:
            cover_data = await self._get_single_image(volume.cover)
        elif self.novel.cover_url:
            cover_data = await self._get_single_image(self.novel.cover_url)

        with zipfile.ZipFile(cbz_path, "a", zipfile.ZIP_DEFLATED) as zf:
            if cover_data:
                page_count += 1
                img_index += 1
                zf.writestr(f"{img_index:05d}.jpg", cover_data)

        for i, chapter in enumerate(volume.chapters):
            if AppConfig().cancelled:
                break

            chapter_name = chapter.chapter_name or f"chapter_{i + 1}"
            if self.on_progress:
                self.on_progress("chapter_start", chapter_name)

            try:
                image_urls = await self.light_novel_source.fetch_chapter_images(chapter.chapter_url)
            except Exception as e:
                logger.error(f"Failed to get images: {e}")
                if self.on_progress:
                    self.on_progress("chapter_fail", str(e))
                continue

            if not image_urls:
                continue

            semaphore = asyncio.Semaphore(4)

            async def _download_one(j: int, img_url: str):
                async with semaphore:
                    try:
                        data = await self.light_novel_source.get_image(img_url)
                        ext = ".jpg"
                        if ".webp" in img_url.lower():
                            ext = ".webp"
                        elif ".png" in img_url.lower():
                            ext = ".png"
                        return (j, data, ext)
                    except Exception:
                        return (j, None, ".jpg")

            tasks = [_download_one(j, url) for j, url in enumerate(image_urls)]
            dl_results = await asyncio.gather(*tasks)

            for j, data, ext in sorted(dl_results, key=lambda x: x[0]):
                if data is None:
                    failed_images.append((chapter_name, j + 1, image_urls[j]))
                    continue
                img_index += 1
                page_count += 1
                fname = f"{img_index:05d}{ext}"
                with zipfile.ZipFile(cbz_path, "a", zipfile.ZIP_DEFLATED) as zf:
                    zf.writestr(fname, data)

            if self.on_progress:
                self.on_progress("chapter_done", chapter_name)

        if failed_images:
            msg = f"警告: {len(failed_images)} 张图片下载失败"
            logger.warning(msg)
            for ch_name, p_idx, img_url in failed_images:
                logger.warning(f"  [{ch_name}] 第{p_idx}页: {img_url[:100]}")
            if self.on_progress:
                self.on_progress("chapter_fail", f"{msg}: {', '.join(f'{ch} p{p}' for ch,p,_ in failed_images)}")

        self._write_comic_info(cbz_path, volume, page_count, cbz_name)

        return os.path.abspath(cbz_path)

    def _write_comic_info(self, cbz_path, volume, page_count, title):
        series_index = VolumeUtil.get_series_index(volume.volume_name)
        comic_info = '<?xml version="1.0"?>\n'
        comic_info += '<ComicInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
        comic_info += f"  <Title>{self._xml_escape(volume.volume_name)}</Title>\n"
        comic_info += f"  <Series>{self._xml_escape(self.novel.title)}</Series>\n"
        if series_index is not None:
            comic_info += f"  <Number>{series_index}</Number>\n"
        if self.novel.author:
            comic_info += f"  <Writer>{self._xml_escape(self.novel.author)}</Writer>\n"
        if self.novel.publisher:
            comic_info += f"  <Publisher>{self._xml_escape(self.novel.publisher)}</Publisher>\n"
        if self.novel.tags:
            comic_info += f"  <Genre>{self._xml_escape(','.join(self.novel.tags))}</Genre>\n"
        if self.novel.description:
            comic_info += f"  <Summary>{self._xml_escape(self.novel.description)}</Summary>\n"
        comic_info += f"  <PageCount>{page_count}</PageCount>\n"
        comic_info += "  <Manga>Yes</Manga>\n"
        comic_info += "</ComicInfo>\n"
        with zipfile.ZipFile(cbz_path, "a", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("ComicInfo.xml", comic_info.encode("utf-8"))

    @staticmethod
    def _xml_escape(text):
        if not isinstance(text, str):
            text = str(text)
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")

    async def _pack_manga_volume(self, volume: Volume) -> str:
        epub_path = self._get_cbz_path(volume).replace(".cbz", ".epub")
        os.makedirs(os.path.dirname(epub_path) or ".", exist_ok=True)

        packer = EpubPacker(epub_path)
        packer.doc_title = f"{volume.catalog.novel.title} {volume.volume_name}"
        if volume.volume_name.startswith(volume.catalog.novel.title):
            packer.doc_title = volume.volume_name
        packer.creator = volume.catalog.novel.author
        packer.source = self.novel.url
        packer.publisher = self.novel.publisher
        packer.subjects = self.novel.tags or []
        packer.description = self.novel.description

        cover_data = None
        if volume.cover:
            cover_data = await self._get_single_image(volume.cover)
        elif self.novel.cover_url:
            cover_data = await self._get_single_image(self.novel.cover_url)
        if cover_data:
            cover_name = f"images/{self._image_sequence.next:06d}.jpg"
            packer.set_cover_image(cover_name, cover_data)

        failed_images = []

        for i, chapter in enumerate(volume.chapters):
            if AppConfig().cancelled:
                logger.info("Cancelled")
                break

            chapter_name = chapter.chapter_name or f"chapter_{i + 1}"
            if self.on_progress:
                self.on_progress("chapter_start", chapter_name)

            try:
                image_urls = await self.light_novel_source.fetch_chapter_images(chapter.chapter_url)
            except Exception as e:
                logger.error(f"Failed to get images: {e}")
                if self.on_progress:
                    self.on_progress("chapter_fail", str(e))
                continue

            if not image_urls:
                continue

            semaphore = asyncio.Semaphore(4)

            async def _download_one(j: int, img_url: str):
                async with semaphore:
                    try:
                        data = await self.light_novel_source.get_image(img_url)
                        return (j, data) if data else (j, None)
                    except Exception:
                        return (j, None)

            tasks = [_download_one(j, url) for j, url in enumerate(image_urls)]
            dl_results = await asyncio.gather(*tasks)

            first_page_href = None
            ch_idx = self._chapter_sequence.next

            for j, data in dl_results:
                if data is None:
                    failed_images.append((chapter_name, j + 1, image_urls[j]))
                    continue
                ext = ".jpg"
                if ".webp" in image_urls[j].lower():
                    ext = ".webp"
                elif ".png" in image_urls[j].lower():
                    ext = ".png"

                img_seq = self._image_sequence.next
                img_rel = f"images/{img_seq:06d}{ext}"
                packer.add_image(f"OEBPS/{img_rel}", data)

                page_name = f"page{ch_idx:06d}_{j:04d}.xhtml"
                page_title = f"第{j+1}页"
                html = (
                    '<?xml version="1.0" encoding="utf-8"?>\n'
                    '<!DOCTYPE html>\n'
                    '<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-CN">\n'
                    f'<head><title>{chapter_name}</title></head>\n'
                    '<body style="margin:0;padding:0;text-align:center;">\n'
                    f'<img src="{img_rel}" alt="第{j+1}页" style="max-width:100%;height:auto;"/>\n'
                    '</body>\n</html>'
                )
                packer.add_chapter(f"OEBPS/{page_name}", page_title, html, add_nav_point=False)
                if j == 0:
                    first_page_href = page_name

            if first_page_href:
                packer.add_nav_point(chapter_name, first_page_href)

            if self.on_progress:
                self.on_progress("chapter_done", chapter_name)

        # Retry failed images one final pass
        if failed_images:
            if self.on_progress:
                self.on_progress("info", f"重试 {len(failed_images)} 张缺失图片...")
            success = []
            for ch_name, p_idx, img_url in failed_images:
                for attempt in range(5):
                    try:
                        data = await self.light_novel_source.get_image(img_url)
                        if data:
                            success.append((ch_name, p_idx, img_url, data))
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(2)
            # Insert recovered images
            for ch_name, p_idx, img_url, data in success:
                failed_images.remove((ch_name, p_idx, img_url))
                ext = ".jpg"
                if ".webp" in img_url.lower():
                    ext = ".webp"
                elif ".png" in img_url.lower():
                    ext = ".png"
                img_seq = self._image_sequence.next
                img_rel = f"images/{img_seq:06d}{ext}"
                packer.add_image(f"OEBPS/{img_rel}", data)
                page_name = f"retry{img_seq:06d}.xhtml"
                page_title = f"第{p_idx}页"
                html = (
                    '<?xml version="1.0" encoding="utf-8"?>\n'
                    '<!DOCTYPE html>\n'
                    '<html xmlns="http://www.w3.org/1999/xhtml" lang="zh-CN">\n'
                    f'<head><title>{ch_name}</title></head>\n'
                    '<body style="margin:0;padding:0;text-align:center;">\n'
                    f'<img src="{img_rel}" alt="第{p_idx}页" style="max-width:100%;height:auto;"/>\n'
                    '</body>\n</html>'
                )
                packer.add_chapter(f"OEBPS/{page_name}", page_title, html, add_nav_point=False)

        # Report final failures
        if failed_images:
            msg = f"警告: {len(failed_images)} 张图片下载失败"
            logger.warning(msg)
            for ch_name, p_idx, img_url in failed_images:
                logger.warning(f"  [{ch_name}] 第{p_idx}页: {img_url[:100]}")
            if self.on_progress:
                self.on_progress("chapter_fail", f"{msg}: {', '.join(f'{ch} p{p}' for ch,p,_ in failed_images)}")

        packer.pack()
        logger.info(f"EPUB: {os.path.abspath(epub_path)}")
        return os.path.abspath(epub_path)

    def _get_cbz_path(self, volume: Volume) -> str:
        out_dir = AppConfig().output_dir or "."
        title = self._sanitize_filename(self.novel.title)
        vol_name = self._sanitize_filename(volume.volume_name)
        if vol_name and title in vol_name:
            return os.path.join(out_dir, title, f"{vol_name}.epub")
        if vol_name:
            return os.path.join(out_dir, title, f"{title} {vol_name}.epub")
        return os.path.join(out_dir, title, f"{title}.epub")
