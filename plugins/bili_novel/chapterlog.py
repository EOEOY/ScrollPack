import re
import asyncio

DOMAIN = "https://www.bilinovel.com"

FALLBACK_TEMPLATE = {
    "fixedLength": 20,
    "seedMultiplier": 135,
    "seedOffset": 234,
    "a": 9302,
    "c": 49397,
    "mod": 233280,
}


class BiliChapterLogResolver:
    def __init__(self, load_script, log_info=None):
        self.domain = DOMAIN
        self.load_script = load_script
        self.log_info = log_info
        self._template_cache = {}
        self._warnings = set()
        self._lock = asyncio.Lock()

    async def get_shuffle_params(self, html: str) -> dict | None:
        from bs4 import BeautifulSoup
        doc = BeautifulSoup(html, 'html.parser')
        script_tag = None
        for s in doc.find_all('script'):
            src = s.get('src', '')
            if 'chapterlog.js?v' in src:
                script_tag = s
                break
        if not script_tag:
            return None

        m = re.search(r"chapterid:'(\d+)'", html)
        if not m:
            return None
        chapter_id = int(m.group(1))

        js_src = script_tag['src']
        script_url = _resolve_url(DOMAIN, js_src)
        template = await self._get_template(script_url)
        if template is None:
            template = FALLBACK_TEMPLATE
        return _to_shuffle_params(template, chapter_id)

    async def _get_template(self, script_url):
        async with self._lock:
            if script_url in self._template_cache:
                return self._template_cache[script_url]
            try:
                js = await self.load_script(script_url)
                template = _try_parse(js)
                if template is None:
                    if script_url not in self._warnings:
                        self._warnings.add(script_url)
                        logger = __import__("logger").logger
                        logger.warning(f"failed to parse chapterlog.js: {script_url}")
                self._template_cache[script_url] = template
                return template
            except Exception as e:
                if script_url not in self._warnings:
                    self._warnings.add(script_url)
                    logger = __import__("logger").logger
                    logger.warning(f"failed to load chapterlog.js: {script_url}")
                if self.log_info:
                    self.log_info(f"chapterlog load error: {e}")
                self._template_cache[script_url] = None
                return None


def _resolve_url(base, rel):
    from urllib.parse import urljoin
    return urljoin(base, rel)


def _to_shuffle_params(template, chapter_id):
    return {
        "fixedLength": template["fixedLength"],
        "seed": chapter_id * template["seedMultiplier"] + template["seedOffset"],
        "a": template["a"],
        "c": template["c"],
        "mod": template["mod"],
    }


class _ExpressionParser:
    def __init__(self, source):
        self.source = source
        self.index = 0

    def parse(self):
        value = self._parse_xor()
        self._skip_ws()
        if self.index != len(self.source):
            raise ValueError(f"Unexpected token at {self.index}")
        return value

    def _parse_xor(self):
        value = self._parse_shift()
        while True:
            self._skip_ws()
            if not self._match('^'):
                return value
            value ^= self._parse_shift()

    def _parse_shift(self):
        value = self._parse_addsub()
        while True:
            self._skip_ws()
            if self._match('<<'):
                value <<= self._parse_addsub()
                continue
            if self._match('>>>') or self._match('>>'):
                value >>= self._parse_addsub()
                continue
            return value

    def _parse_addsub(self):
        value = self._parse_muldivmod()
        while True:
            self._skip_ws()
            if self._match('+'):
                value += self._parse_muldivmod()
                continue
            if self._match('-'):
                value -= self._parse_muldivmod()
                continue
            return value

    def _parse_muldivmod(self):
        value = self._parse_unary()
        while True:
            self._skip_ws()
            if self._match('*'):
                value *= self._parse_unary()
                continue
            if self._match('/'):
                value //= self._parse_unary()
                continue
            if self._match('%'):
                value %= self._parse_unary()
                continue
            return value

    def _parse_unary(self):
        self._skip_ws()
        if self._match('+'):
            return self._parse_unary()
        if self._match('-'):
            return -self._parse_unary()
        if self._match('~'):
            return ~self._parse_unary()
        return self._parse_primary()

    def _parse_primary(self):
        self._skip_ws()
        if self._match('('):
            value = self._parse_xor()
            self._skip_ws()
            if not self._match(')'):
                raise ValueError("Missing closing paren")
            return value
        start = self.index
        while self.index < len(self.source) and self._is_number_char(ord(self.source[self.index])):
            self.index += 1
        if start == self.index:
            raise ValueError("Expected number")
        token = self.source[start:self.index]
        if token.startswith('0x') or token.startswith('0X'):
            return int(token[2:], 16)
        return int(token)

    def _is_number_char(self, c):
        return (48 <= c <= 57) or (65 <= c <= 70) or (97 <= c <= 102) or c == 120 or c == 88

    def _match(self, val):
        if self.source.startswith(val, self.index):
            self.index += len(val)
            return True
        return False

    def _skip_ws(self):
        while self.index < len(self.source):
            c = ord(self.source[self.index])
            if c in (32, 9, 10, 13):
                self.index += 1
            else:
                return


def _eval_int(expression):
    if not expression:
        return None
    try:
        return _ExpressionParser(expression.strip()).parse()
    except Exception:
        return None


def _eval_with_vars(expression, variables):
    normalized = expression
    for k, v in variables.items():
        normalized = re.sub(rf'Number\s*\(\s*{k}\s*\)', str(v), normalized)
        normalized = re.sub(rf'\b{k}\b', str(v), normalized)
    return _eval_int(normalized)


def _strip_outer_parens(s):
    s = s.strip()
    while s.startswith('(') and s.endswith(')'):
        depth = 0
        wraps = True
        for i, ch in enumerate(s):
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0 and i != len(s) - 1:
                    wraps = False
                    break
        if not wraps:
            return s
        s = s[1:-1].strip()
    return s


def _split_top_level(expression, op):
    parts = []
    start = 0
    depth = 0
    op_len = len(op)
    i = 0
    while i < len(expression):
        ch = expression[i]
        if ch == '(':
            depth += 1
            i += 1
            continue
        if ch == ')':
            depth -= 1
            i += 1
            continue
        if depth == 0 and expression.startswith(op, i):
            parts.append(expression[start:i].strip())
            start = i + op_len
            i += op_len
            continue
        i += 1
    parts.append(expression[start:].strip())
    return parts


def _extract_trailing(source, start_pattern, terminator):
    m = re.search(start_pattern, source)
    if not m:
        return None
    start = m.end()
    depth = 0
    for i in range(start, len(source)):
        ch = source[i]
        if ch == '(':
            depth += 1
            continue
        if ch == ')':
            if depth == 0 and terminator == ')':
                return source[start:i].strip()
            depth -= 1
            continue
        if depth == 0 and ch == terminator:
            return source[start:i].strip()
    return None


def _try_parse(js):
    t = _try_parse_plain(js)
    if t:
        return t
    return _try_parse_obfuscated(js)


def _try_parse_plain(js):
    fixed_len_expr = _extract_trailing(js, r'if\s*\(\s*[_$a-zA-Z0-9]+\s*>\s*', ')')
    seed_m = re.search(r'=\s*(.+?Number\s*\(\s*chapterId\s*\).+?)\s*;', js)
    lcg_m = re.search(r'=\s*(\(\s*[_$a-zA-Z0-9]+\s*\*.+?\)\s*%\s*.+?)\s*;', js)
    if not fixed_len_expr or not seed_m or not lcg_m:
        return None
    fixed_len = _eval_int(_strip_outer_parens(fixed_len_expr))
    seed_params = _parse_seed_expr(seed_m.group(1))
    lcg_params = _parse_lcg_expr(lcg_m.group(1))
    if fixed_len is None or seed_params is None or lcg_params is None:
        return None
    return {
        "fixedLength": fixed_len,
        "seedMultiplier": seed_params[0],
        "seedOffset": seed_params[1],
        "a": lcg_params[0],
        "c": lcg_params[1],
        "mod": lcg_params[2],
    }


def _try_parse_obfuscated(js):
    seed_params = _parse_obfuscated_seed(js)
    lcg_params = _parse_obfuscated_lcg(js)
    if not seed_params or not lcg_params:
        return None
    return {
        "fixedLength": FALLBACK_TEMPLATE["fixedLength"],
        "seedMultiplier": seed_params[0],
        "seedOffset": seed_params[1],
        "a": lcg_params[0],
        "c": lcg_params[1],
        "mod": lcg_params[2],
    }


def _parse_seed_expr(expr):
    if not expr:
        return None
    offset = _eval_with_vars(expr, {"chapterId": 0})
    one_val = _eval_with_vars(expr, {"chapterId": 1})
    if offset is None or one_val is None:
        return None
    return (one_val - offset, offset)


def _parse_obfuscated_seed(js):
    pattern = r'var\s+[_$a-zA-Z0-9]+\s*=\s*[^;]*?Number\s*\(\s*[_$a-zA-Z0-9]+\s*\)\s*,\s*([^,)]+?)\s*\)\s*,\s*([^,)]+?)\s*\)\s*,'
    for m in re.finditer(pattern, js):
        multiplier = _eval_int(m.group(1))
        offset = _eval_int(m.group(2))
        if multiplier is None or offset is None or multiplier <= 0 or offset < 0:
            continue
        return (multiplier, offset)
    return None


def _parse_lcg_expr(expr):
    if not expr:
        return None
    parts = _split_top_level(expr, '%')
    if len(parts) != 2:
        return None
    mod = _eval_int(parts[1])
    if mod is None:
        return None
    left = _strip_outer_parens(parts[0])
    var_match = re.search(r'[_$a-zA-Z][_$a-zA-Z0-9]*', left)
    if not var_match:
        return None
    var_name = var_match.group(0)
    c_val = _eval_with_vars(left, {var_name: 0})
    one_val = _eval_with_vars(left, {var_name: 1})
    if c_val is None or one_val is None:
        return None
    return (one_val - c_val, c_val, mod)


def _parse_obfuscated_lcg(js):
    pattern = r'([_$a-zA-Z0-9]+)\s*=\s*[^;]*?\(\s*\1\s*,\s*([^,)]+?)\s*\)\s*,\s*([^,)]+?)\s*\)\s*,\s*([^;)]+?)\s*\)\s*;'
    for m in re.finditer(pattern, js):
        a = _eval_int(m.group(2))
        c = _eval_int(m.group(3))
        mod = _eval_int(m.group(4))
        if a is None or c is None or mod is None or a <= 0 or c < 0 or mod <= a or mod <= c:
            continue
        return (a, c, mod)
    return None
