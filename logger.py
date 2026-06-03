import logging
import os
import sys

if getattr(sys, 'frozen', False):
    _LOG_DIR = os.path.dirname(sys.executable)
else:
    _LOG_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_FILE = os.path.join(_LOG_DIR, "scrollpack.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("scrollpack")
