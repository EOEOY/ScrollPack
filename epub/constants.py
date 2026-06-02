MIMETYPE_STR = "application/epub+zip"

CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
    <rootfiles>
        <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml" />
    </rootfiles>
</container>
"""

STYLE_CSS = """.chapter-title {
    margin-top: 0.5em!important;
    font-size: 1.25em!important;
    font-weight: 800!important;
    text-align: center!important;
}
"""

NCX = "application/x-dtbncx+xml"
XHTML = "application/xhtml+xml"
JPEG = "image/jpeg"
GIF = "image/gif"
PNG = "image/png"
BMP = "image/bmp"
WEBP = "image/webp"
OEBPS = "application/oebps-package+xml"
EPUB = "application/epub+zip"
CSS = "text/css"
TTF = "font/ttf"
