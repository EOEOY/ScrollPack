import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

TO_CLEAN = [
    "bili_novel.log",
    "__pycache__",
    "sources/bili_novel/__pycache__",
    "sources/wenku_novel/__pycache__",
    "sources/copy_manga/__pycache__",
    "sources/__pycache__",
    "epub/__pycache__",
    "utils/__pycache__",
    "web/__pycache__",
]

def clean():
    removed = []
    for pattern in TO_CLEAN:
        path = os.path.join(ROOT, pattern)
        if pattern.endswith("__pycache__"):
            for dirpath, dirnames, _ in os.walk(ROOT):
                for d in dirnames:
                    if d == "__pycache__":
                        full = os.path.join(dirpath, d)
                        shutil.rmtree(full, ignore_errors=True)
                        removed.append(full)
        elif os.path.isfile(path):
            os.remove(path)
            removed.append(path)
        elif os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            removed.append(path)

    for r in removed:
        print(f"  removed: {os.path.relpath(r, ROOT)}")
    print(f"done ({len(removed)} items)")

if __name__ == "__main__":
    clean()
