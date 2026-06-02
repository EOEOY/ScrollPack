import json
import io
import os
import shutil
import zipfile

import httpx

from logger import logger
from config import AppConfig


class PluginRepository:
    def __init__(self, repo_url=None):
        self.repo_url = (repo_url or AppConfig().repo_url).rstrip("/")

    @property
    def _index_url(self):
        return f"{self.repo_url}/index.json"

    async def fetch_index(self):
        import time
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(self._index_url + f"?t={int(time.time())}")
            r.raise_for_status()
            data = r.json()
            return data.get("plugins", data) if isinstance(data, dict) else data

    async def check_updates(self, local_plugins):
        try:
            remote_plugins = await self.fetch_index()
        except Exception as e:
            logger.warning(f"Failed to fetch remote index: {e}")
            raise

        remote_map = {p["id"]: p for p in remote_plugins}
        updates = []
        new_plugins = []

        for rp in remote_plugins:
            rid = rp["id"]
            local = next((p for p in local_plugins if p["id"] == rid), None)
            if local:
                if _version_gt(rp.get("version", "0"), local.get("version", "0")):
                    updates.append(_make_entry(rp, local=local))
            else:
                new_plugins.append(_make_entry(rp))

        # Full list: all remote plugins with local status
        full = []
        for rp in remote_plugins:
            local = next((p for p in local_plugins if p["id"] == rp["id"]), None)
            full.append(_make_entry(rp, local=local))

        return {"updates": updates, "new": new_plugins, "all": full}

    async def download_plugin(self, download_url, max_retries=3):
        import asyncio
        url = download_url
        if not url.startswith("http"):
            url = f"{self.repo_url}/{url.lstrip('/')}"
        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                    r = await client.get(url)
                    r.raise_for_status()
                    return r.content
            except Exception as e:
                last_err = e
                if attempt < max_retries:
                    logger.warning(f"Plugin download retry {attempt}/{max_retries}: {e}")
                    await asyncio.sleep(2 * attempt)
        raise last_err

    async def install_plugin(self, download_url, plugins_dir):
        data = await self.download_plugin(download_url)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            members = zf.namelist()
            root_dirs = sorted(set(
                m.split("/")[0] for m in members
                if m.endswith("/") or "/" in m
            ))
            if not root_dirs:
                raise ValueError("Invalid plugin zip: no root directory")
            plugin_name = root_dirs[0]
            dest = os.path.join(plugins_dir, plugin_name)
            if os.path.exists(dest):
                shutil.rmtree(dest)
            os.makedirs(dest, exist_ok=True)
            for m in members:
                rel = m.split("/", 1)[1] if "/" in m else ""
                if not rel:
                    continue
                target = os.path.join(dest, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(m) as src, open(target, "wb") as dst:
                    dst.write(src.read())
        return plugin_name


def _make_entry(rp, local=None):
    entry = {
        "id": rp["id"],
        "name": rp.get("name", rp["id"]),
        "version": rp.get("version", ""),
        "type": rp.get("type", ""),
        "download": rp.get("download", ""),
        "description": rp.get("description", ""),
        "author": rp.get("author", ""),
    }
    if local:
        entry["local_version"] = local.get("version", "")
        entry["installed"] = True
    else:
        entry["installed"] = False
    return entry


def _version_gt(a, b):
    try:
        pa = [int(x) for x in str(a).split(".")]
        pb = [int(x) for x in str(b).split(".")]
        while len(pa) < len(pb):
            pa.append(0)
        while len(pb) < len(pa):
            pb.append(0)
        for va, vb in zip(pa, pb):
            if va > vb:
                return True
            if va < vb:
                return False
        return False
    except (ValueError, AttributeError):
        return a != b
