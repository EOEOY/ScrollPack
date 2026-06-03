import os
import uuid
from datetime import datetime, timezone
from ebooklib import epub

from .constants import XHTML, JPEG, CSS, STYLE_CSS


class EpubPacker:
    def __init__(self, epub_file_path: str):
        self.epub_file_path = epub_file_path
        self.book = epub.EpubBook()
        self.book.set_identifier(str(uuid.uuid4()))
        self.book.set_language('zh-CN')
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.book.add_metadata('DC', 'date', now)
        self._spine = ['nav']
        self._toc = []
        self._added = set()
        self._cover_file = None
        self._cover_image_data = None
        self._calibre_series = None
        self._calibre_series_index = None

    @property
    def absolute_path(self):
        return os.path.abspath(self.epub_file_path)

    @property
    def doc_title(self):
        return self.book.title or ""

    @doc_title.setter
    def doc_title(self, val):
        self.book.title = val

    @property
    def creator(self):
        items = self.book.get_metadata('DC', 'creator')
        return items[0][0] if items else ""

    @creator.setter
    def creator(self, val):
        self.book.add_metadata('DC', 'creator', val)

    @property
    def source(self):
        items = self.book.get_metadata('DC', 'source')
        return items[0][0] if items else None

    @source.setter
    def source(self, val):
        if val:
            self.book.add_metadata('DC', 'source', val)

    @property
    def publisher(self):
        items = self.book.get_metadata('DC', 'publisher')
        return items[0][0] if items else None

    @publisher.setter
    def publisher(self, val):
        if val:
            self.book.add_metadata('DC', 'publisher', val)

    @property
    def subjects(self):
        return [item[0] for item in self.book.get_metadata('DC', 'subject')]

    @subjects.setter
    def subjects(self, val):
        for v in val:
            self.book.add_metadata('DC', 'subject', v)

    @property
    def description(self):
        items = self.book.get_metadata('DC', 'description')
        return items[0][0] if items else None

    @description.setter
    def description(self, val):
        if val:
            self.book.add_metadata('DC', 'description', val)

    @property
    def cover(self):
        return self._cover_file

    @cover.setter
    def cover(self, val):
        self._cover_file = val

    def set_cover_image(self, name, data):
        self._cover_file = name
        self._cover_image_data = data

    @property
    def calibre_series(self):
        return self._calibre_series

    @calibre_series.setter
    def calibre_series(self, val):
        self._calibre_series = val

    @property
    def calibre_series_index(self):
        return self._calibre_series_index

    @calibre_series_index.setter
    def calibre_series_index(self, val):
        self._calibre_series_index = val

    def _handle_id(self, id_str):
        for ch in "\\/.":
            id_str = id_str.replace(ch, "_")
        return id_str

    def add_chapter(self, name, title, chapter_content, add_nav_point=True, media_type=XHTML):
        href = os.path.relpath(name, "OEBPS").replace("\\", "/")
        uid = self._handle_id(href)
        chapter = epub.EpubHtml(title=title, file_name=href, lang='zh-CN')
        chapter.content = chapter_content.encode("utf-8")
        chapter.media_type = media_type
        self.book.add_item(chapter)
        self._spine.append(chapter)
        if add_nav_point:
            self._toc.append(epub.Link(href, title, uid))

    def add_image(self, name, data, media_type=JPEG):
        href = os.path.relpath(name, "OEBPS").replace("\\", "/")
        uid = self._handle_id(href)
        img = epub.EpubImage()
        img.file_name = href
        img.media_type = media_type
        img.content = data
        img.id = uid
        self.book.add_item(img)

    def add_stylesheet(self):
        css = epub.EpubItem(
            uid="style",
            file_name="styles/style.css",
            media_type=CSS,
            content=STYLE_CSS.encode("utf-8"),
        )
        self.book.add_item(css)

    def add_nav_point(self, title, src=None):
        if src:
            uid = self._handle_id(src)
            self._toc.append(epub.Link(src, title, uid))
        else:
            self._toc.append(epub.Section(title))

    def pack(self):
        os.makedirs(os.path.dirname(self.epub_file_path) or ".", exist_ok=True)
        self.book.toc = self._toc
        self.book.spine = self._spine

        nav = epub.EpubNav()
        self.book.add_item(nav)
        ncx = epub.EpubNcx()
        self.book.add_item(ncx)

        if self._cover_image_data:
            self.book.set_cover(self._cover_file, self._cover_image_data, create_page=False)
        elif self._cover_file:
            cover_id = self._handle_id(self._cover_file)
            self.book.add_metadata(None, 'meta', '', {'name': 'cover', 'content': cover_id})

        if self._calibre_series:
            self.book.add_metadata(None, 'meta', '', {
                'name': 'calibre:series', 'content': self._calibre_series
            })
        if self._calibre_series_index is not None:
            self.book.add_metadata(None, 'meta', '', {
                'name': 'calibre:series_index', 'content': str(self._calibre_series_index)
            })

        epub.write_epub(self.epub_file_path, self.book)
