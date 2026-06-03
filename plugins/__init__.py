import os
import sys
import json
import importlib
import zipfile
import shutil
from typing import List

from logger import logger
from config import AppConfig

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

if getattr(sys, 'frozen', False):
    exe_dir = os.path.dirname(sys.executable)
    ext = os.path.join(exe_dir, 'plugins')
    if os.path.isdir(ext) and ext not in __path__:
        __path__.append(ext)


def _get_external_plugin_dirs():
    dirs = [PLUGIN_DIR]
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        ext = os.path.join(exe_dir, 'plugins')
        if os.path.isdir(ext) and ext != PLUGIN_DIR:
            sys.path.insert(0, exe_dir)
            dirs.append(ext)
    return dirs


def discover_plugins() -> List[dict]:
    plugins = []
    seen = set()

    for plugin_dir in _get_external_plugin_dirs():
        if not os.path.isdir(plugin_dir):
            continue

        for name in sorted(os.listdir(plugin_dir)):
            if name.startswith("."):
                continue
            full = os.path.join(plugin_dir, name)

            if name.endswith(".zip") and os.path.isfile(full):
                try:
                    with zipfile.ZipFile(full) as zf:
                        try:
                            meta = json.loads(zf.read("plugin.json").decode("utf-8"))
                        except KeyError:
                            candidates = [n for n in zf.namelist() if n.endswith("plugin.json")]
                            if not candidates:
                                continue
                            meta = json.loads(zf.read(candidates[0]).decode("utf-8"))
                    pid = meta.get("id", name[:-4])
                    extract_to = os.path.join(plugin_dir, pid)
                    if not os.path.isdir(extract_to):
                        logger.info(f"Extracting plugin: {pid} from {name}")
                        with zipfile.ZipFile(full) as zf:
                            members = zf.namelist()
                            prefix = ""
                            for m in members:
                                parts = m.split("/")
                                if parts[0] and not parts[0].startswith("__") and f"{parts[0]}/plugin.json" in members:
                                    prefix = parts[0] + "/"
                                    break
                            for m in members:
                                if m.endswith("/"):
                                    continue
                                rel = m[len(prefix):] if prefix else m
                                target = os.path.join(extract_to, rel)
                                os.makedirs(os.path.dirname(target), exist_ok=True)
                                with zf.open(m) as src, open(target, "wb") as dst:
                                    dst.write(src.read())
                    os.remove(full)
                except Exception as e:
                    logger.warning(f"Failed to extract plugin zip {name}: {e}")
                continue

            if name in seen:
                continue
            d = full
            if not os.path.isdir(d):
                continue
            manifest = os.path.join(d, "plugin.json")
            if not os.path.isfile(manifest):
                continue
            try:
                with open(manifest, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                meta["_dir"] = d
                meta["_path"] = name
                plugins.append(meta)
                seen.add(name)
            except Exception as e:
                logger.warning(f"Failed to load plugin manifest {manifest}: {e}")

    return plugins


def load_source(plugin: dict):
    cfg = AppConfig()
    disabled = cfg.disabled_plugins or []
    pid = plugin.get("id", "")
    if pid in disabled:
        return None

    module_name = plugin.get("module", "source")
    class_name = plugin.get("class", "")
    pkg = plugin["_path"]
    plugin_dir = plugin["_dir"]
    encrypted = plugin.get("encrypted", False)

    try:
        if encrypted:
            from . import crypto_loader
            mod = crypto_loader.load_encrypted_plugin(plugin_dir, pkg, module_name)
            logger.info(f"Loaded encrypted plugin: {pid} from plugins.{pkg}.{module_name}")
        else:
            logger.info(f"Loading plugin: {pid} from plugins.{pkg}.{module_name}")
            mod = importlib.import_module(f"plugins.{pkg}.{module_name}")
        cls = getattr(mod, class_name)
        return cls()
    except Exception as e:
        import traceback
        logger.warning(f"Failed to load plugin {pid}: {e}")
        logger.warning(traceback.format_exc())
        return None


def load_all_sources():
    sources = []
    for plugin in discover_plugins():
        src = load_source(plugin)
        if src:
            sources.append(src)
    return sources
