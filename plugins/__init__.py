import os
import json
import importlib
import glob
from typing import List

from logger import logger
from config import AppConfig

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))


def discover_plugins() -> List[dict]:
    plugins = []
    if not os.path.isdir(PLUGIN_DIR):
        return plugins

    for name in sorted(os.listdir(PLUGIN_DIR)):
        d = os.path.join(PLUGIN_DIR, name)
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
            meta["_encrypted"] = len(glob.glob(os.path.join(d, "*.pye"))) > 0
            plugins.append(meta)
        except Exception as e:
            logger.warning(f"Failed to load plugin manifest {manifest}: {e}")

    return plugins


def _try_import_encrypted(pkg, module_name, plugin_dir):
    try:
        from scrollpack_crypto import load_encrypted_module
        return load_encrypted_module(plugin_dir, module_name)
    except ImportError:
        logger.warning("scrollpack_crypto not found, encrypted plugins will be skipped")
        return None
    except Exception as e:
        logger.warning(f"Failed to load encrypted module {pkg}.{module_name}: {e}")
        return None


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

    try:
        if plugin.get("_encrypted"):
            mod = _try_import_encrypted(pkg, module_name, plugin_dir)
            if mod is None:
                return None
        else:
            mod = importlib.import_module(f"plugins.{pkg}.{module_name}")
        cls = getattr(mod, class_name)
        return cls()
    except Exception as e:
        logger.warning(f"Failed to load plugin {pid}: {e}")
        return None


def load_all_sources():
    sources = []
    for plugin in discover_plugins():
        src = load_source(plugin)
        if src:
            sources.append(src)
    return sources
