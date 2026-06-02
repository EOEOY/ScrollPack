import asyncio
import httpx

from config import AppConfig
from logger import logger

DEFAULT_TIMEOUT = 30
DEFAULT_MAX_ATTEMPTS = 5

RETRY_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.TimeoutException,
    httpx.RemoteProtocolError,
    httpx.HTTPStatusError,
    ConnectionError,
    OSError,
)


def _build_proxy_url():
    config = AppConfig()
    if config.has_proxy:
        proxy_url = f"http://{config.proxy_host}:{config.proxy_port}"
        if config.proxy_username and config.proxy_password:
            proxy_url = f"http://{config.proxy_username}:{config.proxy_password}@{config.proxy_host}:{config.proxy_port}"
        logger.info(f"Using proxy: {config.proxy_host}:{config.proxy_port}")
        return proxy_url
    return None


async def _async_retry(fn, max_attempts=DEFAULT_MAX_ATTEMPTS):
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return await fn()
        except RETRY_EXCEPTIONS as e:
            last_exc = e
            if attempt < max_attempts - 1:
                await asyncio.sleep(3)
    raise last_exc


async def http_get_string(
    url,
    headers=None,
    timeout=DEFAULT_TIMEOUT,
    encoding="utf-8",
    max_attempts=None,
):
    async def _fn():
        proxy = _build_proxy_url()
        async with httpx.AsyncClient(proxy=proxy, timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            content = response.content
            try:
                return content.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                return content.decode("utf-8", errors="replace")
    return await _async_retry(_fn, max_attempts or DEFAULT_MAX_ATTEMPTS)


async def http_get_bytes(
    url,
    headers=None,
    timeout=DEFAULT_TIMEOUT,
    max_attempts=None,
):
    async def _fn():
        proxy = _build_proxy_url()
        async with httpx.AsyncClient(proxy=proxy, timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            return response.content
    return await _async_retry(_fn, max_attempts or DEFAULT_MAX_ATTEMPTS)
