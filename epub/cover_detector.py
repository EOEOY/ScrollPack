import io
import struct

from .constants import JPEG, GIF, PNG, BMP, WEBP


class UnsupportedImageException(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(message)


class ImageInfo:
    def __init__(self, width, height, mime_type):
        self.width = width
        self.height = height
        self.mime_type = mime_type

    @property
    def ratio(self):
        return self.width / self.height if self.height else 0


class LightNovelCoverDetector:
    COVER_RATIO = 3 / 4

    def __init__(self):
        self._image_info = {}
        self._image_data = {}

    def add(self, name, data):
        try:
            info = _get_image_info(io.BytesIO(data))
            self._image_info[name] = info
            self._image_data[name] = data
        except UnsupportedImageException:
            pass

    def detect_cover(self):
        if not self._image_info:
            return None
        for name, info in self._image_info.items():
            if info.ratio < 1:
                return name, self._image_data.get(name)
        name = next(iter(self._image_info.keys()))
        return name, self._image_data.get(name)


def _read_int(f, count, big_endian):
    result = 0
    if big_endian:
        for _ in range(count):
            result = (result << 8) | f.read(1)[0]
    else:
        for i in range(count):
            result |= f.read(1)[0] << (i * 8)
    return result


def _get_image_info(stream):
    c1 = stream.read(1)[0]
    c2 = stream.read(1)[0]
    c3 = stream.read(1)[0]

    if c1 == 0x47 and c2 == 0x49 and c3 == 0x46:  # GIF
        stream.read(3)
        width = struct.unpack('<H', stream.read(2))[0]
        height = struct.unpack('<H', stream.read(2))[0]
        return ImageInfo(width, height, GIF)

    if c1 == 0xFF and c2 == 0xD8:  # JPEG
        while c3 == 0xFF:
            marker = stream.read(1)[0]
            length = struct.unpack('>H', stream.read(2))[0]
            if marker in (0xC0, 0xC1, 0xC2):
                stream.read(1)
                height = struct.unpack('>H', stream.read(2))[0]
                width = struct.unpack('>H', stream.read(2))[0]
                return ImageInfo(width, height, JPEG)
            stream.read(length - 2)
            c3 = stream.read(1)[0]

    if c1 == 137 and c2 == 80 and c3 == 78:  # PNG
        stream.read(15)
        width = struct.unpack('>I', stream.read(4))[0]
        stream.read(2)
        height = struct.unpack('>I', stream.read(4))[0]
        return ImageInfo(width, height, PNG)

    if c1 == 66 and c2 == 77:  # BMP
        stream.read(15)
        width = struct.unpack('<I', stream.read(4))[0]
        stream.read(2)
        height = struct.unpack('<I', stream.read(4))[0]
        return ImageInfo(width, height, BMP)

    if c1 == 0x52 and c2 == 0x49 and c3 == 0x46:  # WEBP (RIFF)
        data = stream.read(27)
        width = (data[24] << 8) | data[23]
        height = (data[26] << 8) | data[25]
        return ImageInfo(width, height, WEBP)

    raise UnsupportedImageException(
        f"unsupported image type (0x{c1:02x} 0x{c2:02x} 0x{c3:02x})"
    )
