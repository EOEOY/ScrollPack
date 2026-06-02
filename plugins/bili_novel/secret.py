import re
from utils.http_util import http_get_string

DOMAIN = "https://www.bilinovel.com"


async def get_secret_map() -> dict:
    url = f"{DOMAIN}/themes/zhmb/js/readtools.js"
    js = await http_get_string(url)
    data = _extract_data(js)
    decrypted = _decrypt(data)
    return _to_map(decrypted)


def _extract_data(js: str) -> str:
    before = """['\\x61\\x70\\x70\\x6c\\x79'](null,\""""
    after = """\"['\\x73\\x70\\x6c\\x69\\x74']"""
    start = js.find(before)
    end = js.rfind(after)
    if start == -1 or end == -1 or start >= end:
        raise ValueError(f"Failed to extract secret data from readtools.js (len={len(js)})")
    return js[start + len(before):end]


def _decrypt(data: str) -> str:
    result = []
    code = []
    code_upper_a = ord('A')
    code_upper_z = ord('Z')
    code_lower_a = ord('a')
    code_lower_z = ord('z')
    for ch in data:
        char_code = ord(ch)
        if ((code_upper_a <= char_code <= code_upper_z) or
                (code_lower_a <= char_code <= code_lower_z)):
            if code:
                result.append(chr(int(''.join(code))))
                code = []
        else:
            code.append(ch)
    return ''.join(result)


def _to_map(js_code: str) -> dict:
    result = {}
    js_code = js_code.replace("\\'", '"').replace("'", '"')
    splits = js_code.split(".replace")
    prefix = 'RegExp("'
    for split in splits:
        start = split.find(prefix)
        if start == -1:
            continue
        key = split[start + len(prefix):start + len(prefix) + 1]
        for suffix in ('), "', '),"'):
            s = split.find(suffix)
            if s != -1:
                value = split[s + len(suffix):s + len(suffix) + 1]
                result[key] = value
                break
    return result
