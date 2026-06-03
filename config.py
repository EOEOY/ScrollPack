import json
import os
import sys
from logger import logger

if getattr(sys, 'frozen', False):
    _CONFIG_DIR = os.path.dirname(sys.executable)
else:
    _CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))

os.makedirs(_CONFIG_DIR, exist_ok=True)
_CONFIG_PATH = os.path.join(_CONFIG_DIR, "packer_config.json")


class AppConfig:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self.proxy_host = None
        self.proxy_port = None
        self.proxy_username = None
        self.proxy_password = None
        self.enable_proxy = False
        self.disabled_plugins = []
        self.max_retries = 5
        self.retry_delay_seconds = 3
        self.output_dir = "."
        self.headless = True
        self.combine_volume = False
        self.add_chapter_title = True
        self.repo_url = "https://raw.githubusercontent.com/EOEOY/ScrollPack-plugins/master"
        self._cancel = False
        self._load()

    def request_cancel(self):
        self._cancel = True

    def reset_cancel(self):
        self._cancel = False

    @property
    def cancelled(self):
        return self._cancel

    @property
    def has_proxy(self):
        return self.enable_proxy and bool(self.proxy_host and self.proxy_port)

    def _load(self):
        try:
            if os.path.exists(_CONFIG_PATH):
                with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in data.items():
                    if hasattr(self, k):
                        setattr(self, k, v)
        except Exception as e:
            logger.warning(f"Failed to load config: {e}")

    def save(self):
        data = {
            "headless": self.headless,
            "output_dir": self.output_dir,
            "max_retries": self.max_retries,
            "retry_delay_seconds": self.retry_delay_seconds,
            "proxy_host": self.proxy_host,
            "proxy_port": self.proxy_port,
            "proxy_username": self.proxy_username,
            "proxy_password": self.proxy_password,
            "enable_proxy": self.enable_proxy,
            "disabled_plugins": self.disabled_plugins,
            "combine_volume": self.combine_volume,
            "add_chapter_title": self.add_chapter_title,
            "repo_url": self.repo_url,
        }
        try:
            with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save config: {e}")
