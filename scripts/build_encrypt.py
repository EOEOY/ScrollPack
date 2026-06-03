"""Build helper: encrypt a plugin and copy to dist/plugins/
Usage: python scripts/build_encrypt.py plugins/bili_novel dist/plugins/bili_novel
"""
import sys
import os
import shutil
import tempfile
import zipfile
import io

TOOL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "ScrollPack-encrypt-tool")
sys.path.insert(0, TOOL_DIR)

from encrypt_server import encrypt_plugin


def main():
    if len(sys.argv) != 3:
        print("Usage: python build_encrypt.py <src_plugin_dir> <dest_plugin_dir>")
        sys.exit(1)

    src = sys.argv[1]
    dest = sys.argv[2]

    if not os.path.isdir(src):
        print(f"[ERROR] Source not found: {src}")
        sys.exit(1)

    print(f"  Encrypting: {src} -> {dest}")

    zip_data = encrypt_plugin(src)

    tmpdir = tempfile.mkdtemp()
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            zf.extractall(tmpdir)
        dirs = [d for d in os.listdir(tmpdir) if os.path.isdir(os.path.join(tmpdir, d))]
        if len(dirs) != 1:
            print(f"[ERROR] Unexpected zip structure: {dirs}")
            sys.exit(1)
        src_extracted = os.path.join(tmpdir, dirs[0])
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.copytree(src_extracted, dest)
        print(f"  Done: {len(os.listdir(dest))} files")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
