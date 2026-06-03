import io
import json
import os
import re
import sys
import glob
import shutil
import tempfile
import zipfile
import datetime
import asyncio
import threading

import httpx

from flask import Flask, request, jsonify, send_from_directory

if not getattr(sys, 'frozen', False):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_BASE_DIR = os.environ.get('SCROLLPACK_BASE_DIR', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PLUGINS_DIR = os.path.join(os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else _BASE_DIR, 'plugins')

from config import AppConfig
from novel_packer import NovelPacker
from pack_argument import PackArgument
from plugins.repository import PluginRepository

app = Flask(__name__, static_folder=None)

_tasks = {}
_task_counter = 0


class TaskState:
    def __init__(self, task_id, status):
        self.id = task_id
        self.status = status
        self.logs = []
        self.packer = None
        self.novel = None
        self.catalog = None
        self.output_files = []
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    @property
    def cancelled(self):
        return self._cancelled

    def add_log(self, type, message):
        self.logs.append({
            "type": type,
            "message": message,
            "time": datetime.datetime.now().isoformat(),
        })


def _run_pack_task(task, url, params, selected_chapters, combine_volume, add_chapter_title, output_format):
    try:
        packer = NovelPacker.from_url(url)
        task.packer = packer
        packer.on_progress = lambda type, msg: task.add_log(type, msg)

        async def _do_pack():
            await packer.init()
            task.novel = packer.novel
            task.catalog = packer.catalog

            selected_chs = []
            if selected_chapters:
                chapter_map = {}
                for vol in packer.catalog.volumes:
                    for ch in vol.chapters:
                        chapter_map[ch.chapter_url] = ch
                for u in selected_chapters:
                    ch = chapter_map.get(u)
                    if ch:
                        selected_chs.append(ch)

            if not selected_chs:
                task.status = "error"
                task.add_log("error", "未选择任何章节")
                return

            from models import Volume

            if combine_volume:
                vol_name = "打包"
                nums = []
                for ch in selected_chs:
                    m = re.search(r"第\s*(\d+[\.\d]*)\s*", ch.chapter_name)
                    if m:
                        nums.append(m.group(1))
                if nums and len(nums) == len(selected_chs):
                    try:
                        sorted_nums = sorted(nums, key=lambda x: tuple(map(int, x.split("."))))
                        if len(sorted_nums) == 1:
                            vol_name = f"第{sorted_nums[0]}话"
                        else:
                            vol_name = f"第{sorted_nums[0]}-{sorted_nums[-1]}话"
                    except ValueError:
                        pass
                vol = Volume(vol_name, packer.catalog)
                vol.chapters = selected_chs
                volumes = [vol]
                arg = PackArgument(
                    add_chapter_title=add_chapter_title,
                    combine_volume=True,
                    pack_volumes=volumes,
                    output_format=output_format,
                )
                task.add_log("info", "开始打包...")
                task.output_files = await packer.pack(arg)
                task.status = "done"
                task.add_log("info", "全部打包完成!")
            else:
                vol_groups = {}
                for ch in selected_chs:
                    vol_key = ch.volume.volume_name
                    if vol_key not in vol_groups:
                        vol_groups[vol_key] = []
                    vol_groups[vol_key].append(ch)
                for vol_name, chapters in vol_groups.items():
                    if AppConfig().cancelled:
                        task.add_log("info", "已取消")
                        break
                    vol = Volume(vol_name, packer.catalog)
                    vol.chapters = chapters
                    arg = PackArgument(
                        add_chapter_title=add_chapter_title,
                        combine_volume=False,
                        pack_volumes=[vol],
                        output_format=output_format,
                    )
                    task.add_log("info", f"开始打包: {vol_name}")
                    files = await packer.pack(arg)
                    task.output_files.extend(files)
                    task.add_log("info", f"打包完成: {vol_name}")
                task.status = "done"
                task.add_log("info", "全部打包完成!")

        asyncio.run(_do_pack())
    except Exception as e:
        task.status = "error"
        task.add_log("error", str(e))


def _apply_config(params):
    config = AppConfig()
    config.proxy_host = params.get("proxyHost")
    config.proxy_port = params.get("proxyPort")
    if config.proxy_port and isinstance(config.proxy_port, str):
        config.proxy_port = int(config.proxy_port) if config.proxy_port else None
    config.proxy_username = params.get("proxyUsername")
    config.proxy_password = params.get("proxyPassword")
    config.enable_proxy = params.get("enableProxy", config.enable_proxy)
    config.max_retries = int(params.get("maxRetries", 5))
    config.retry_delay_seconds = int(params.get("retryDelaySeconds", 3))
    config.output_dir = params.get("outputDir") or "."
    config.headless = params.get("headless", True)


def _cors_response(data, status=200):
    resp = jsonify(data)
    resp.status_code = status
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.route("/api/settings", methods=["GET"])
def handle_get_settings():
    cfg = AppConfig()
    return _cors_response({
        "headless": cfg.headless,
        "outputDir": cfg.output_dir,
        "maxRetries": cfg.max_retries,
        "retryDelaySeconds": cfg.retry_delay_seconds,
        "proxyHost": cfg.proxy_host,
        "proxyPort": cfg.proxy_port,
        "proxyUsername": cfg.proxy_username,
        "proxyPassword": cfg.proxy_password,
        "enableProxy": cfg.enable_proxy,
        "combineVolume": cfg.combine_volume,
        "addChapterTitle": cfg.add_chapter_title,
        "repoUrl": cfg.repo_url,
    })


@app.route("/api/settings", methods=["POST"])
def handle_save_settings():
    try:
        params = request.get_json(force=True)
        cfg = AppConfig()
        cfg.headless = params.get("headless", cfg.headless)
        cfg.output_dir = params.get("outputDir", cfg.output_dir)
        cfg.max_retries = int(params.get("maxRetries", cfg.max_retries))
        cfg.retry_delay_seconds = int(params.get("retryDelaySeconds", cfg.retry_delay_seconds))
        cfg.proxy_host = params.get("proxyHost", cfg.proxy_host)
        cfg.proxy_port = params.get("proxyPort", cfg.proxy_port)
        if cfg.proxy_port and isinstance(cfg.proxy_port, str):
            cfg.proxy_port = int(cfg.proxy_port) if cfg.proxy_port else None
        cfg.proxy_username = params.get("proxyUsername", cfg.proxy_username)
        cfg.proxy_password = params.get("proxyPassword", cfg.proxy_password)
        cfg.enable_proxy = params.get("enableProxy", cfg.enable_proxy)
        cfg.combine_volume = params.get("combineVolume", cfg.combine_volume)
        cfg.add_chapter_title = params.get("addChapterTitle", cfg.add_chapter_title)
        cfg.repo_url = params.get("repoUrl", cfg.repo_url)
        cfg.save()
        return _cors_response({"status": "saved"})
    except Exception as e:
        return _cors_response({"error": str(e)}, 500)


@app.route("/api/test-connection", methods=["GET"])
def handle_test_connection():
    results = []
    targets = [
        ("拷贝漫画-主站", "https://www.2026copy.com/"),
        ("拷贝漫画-mangacopy", "https://www.mangacopy.com/"),
        ("哔哩轻小说", "https://www.bilinovel.com/"),
    ]
    for name, url in targets:
        ok = False
        detail = ""
        try:
            r = httpx.get(url, timeout=10, follow_redirects=True)
            ok = r.status_code < 400
            detail = f"HTTP {r.status_code}"
        except Exception as e:
            detail = str(e)
            if "ssl" in str(e).lower() or "eof" in str(e).lower():
                try:
                    from playwright.sync_api import sync_playwright
                    with sync_playwright() as p:
                        args = ["--disable-blink-features=AutomationControlled", "--headless=new"]
                        browser = None
                        try:
                            browser = p.chromium.launch(headless=True, channel="msedge", args=args)
                        except Exception:
                            try:
                                browser = p.chromium.launch(headless=True, channel="chrome", args=args)
                            except Exception:
                                browser = p.chromium.launch(headless=True, args=args)
                        page = browser.new_page()
                        resp = page.goto(url, wait_until="domcontentloaded", timeout=15000)
                        ok = resp and resp.status < 400
                        detail = f"Browser HTTP {resp.status}" if resp else "Browser no response"
                        browser.close()
                except Exception as e2:
                    detail = f"Browser also failed: {e2}"
        results.append({"name": name, "url": url, "ok": ok, "detail": detail})

    try:
        import glob as _g, os as _os
        pdir = _BASE_DIR
        bdir = _os.path.join(pdir, "playwright_browsers")
        if _os.path.isdir(bdir):
            _os.environ["PLAYWRIGHT_BROWSERS_PATH"] = bdir
        from playwright.async_api import async_playwright
        args_list = ["--disable-blink-features=AutomationControlled", "--headless=new"]
        async def _test():
            async with async_playwright() as p:
                browser = None
                engine = ""
                try:
                    browser = await p.chromium.launch(headless=True, channel="msedge", args=args_list)
                    engine = "System Microsoft Edge"
                except Exception:
                    try:
                        browser = await p.chromium.launch(headless=True, channel="chrome", args=args_list)
                        engine = "System Google Chrome"
                    except Exception:
                        hits = _g.glob(_os.path.join(bdir, "chromium-*", "chrome-win*", "chrome.exe"))
                        exe = hits[0] if hits else None
                        browser = await p.chromium.launch(headless=True, executable_path=exe, args=args_list)
                        engine = "Bundled Chromium"
                await browser.close()
                return engine
        engine = asyncio.run(_test())
        results.append({"name": "Playwright", "url": "chromium", "ok": True, "detail": f"OK ({engine})"})
    except Exception as e:
        results.append({"name": "Playwright", "url": "", "ok": False, "detail": str(e)[:120]})

    return _cors_response({"results": results})


@app.route("/api/plugins", methods=["GET"])
def handle_plugins():
    from plugins import discover_plugins
    cfg = AppConfig()
    disabled = cfg.disabled_plugins or []
    plugins = []
    for p in discover_plugins():
        plugins.append({
            "id": p.get("id", ""),
            "name": p.get("name", ""),
            "version": p.get("version", ""),
            "type": p.get("type", ""),
            "enabled": p.get("id") not in disabled,
            "encrypted": p.get("_encrypted", False),
        })
    return _cors_response({"plugins": plugins})


@app.route("/api/plugins/toggle", methods=["POST"])
def handle_toggle_plugin():
    try:
        params = request.get_json(force=True)
        pid = params.get("id", "")
        enabled = params.get("enabled", True)
        cfg = AppConfig()
        disabled = cfg.disabled_plugins or []
        if enabled and pid in disabled:
            disabled.remove(pid)
        elif not enabled and pid not in disabled:
            disabled.append(pid)
        cfg.disabled_plugins = disabled
        cfg.save()
        return _cors_response({"status": "ok"})
    except Exception as e:
        return _cors_response({"error": str(e)}, 500)


@app.route("/api/plugins/delete", methods=["POST"])
def handle_delete_plugin():
    try:
        params = request.get_json(force=True)
        pid = params.get("id", "")
        if not pid or ".." in pid or "/" in pid or "\\" in pid:
            return _cors_response({"error": "invalid id"}, 400)
        plugin_dir = os.path.join(_PLUGINS_DIR, pid)
        if not os.path.isdir(plugin_dir):
            return _cors_response({"error": "plugin not found"}, 404)
        shutil.rmtree(plugin_dir)
        return _cors_response({"status": "ok"})
    except Exception as e:
        return _cors_response({"error": str(e)}, 500)


@app.route("/api/plugins/export", methods=["GET"])
def handle_export_plugin():
    pid = request.args.get("plugin", "")
    if not pid or ".." in pid or "/" in pid or "\\" in pid:
        return _cors_response({"error": "invalid id"}, 400)

    plugin_dir = os.path.join(_BASE_DIR, "plugins", pid)
    if not os.path.isdir(plugin_dir):
        return _cors_response({"error": "plugin not found"}, 404)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(plugin_dir):
            for f in files:
                fp = os.path.join(root, f)
                arcname = os.path.relpath(fp, os.path.dirname(plugin_dir))
                zf.write(fp, arcname)
    buf.seek(0)

    from flask import Response
    return Response(buf.read(), mimetype="application/zip",
                    headers={"Content-Disposition": f"attachment; filename={pid}.zip"})


@app.route("/api/plugins/import", methods=["POST"])
def handle_import_plugin():
    if "file" not in request.files:
        return _cors_response({"error": "no file"}, 400)

    f = request.files["file"]
    if not f.filename or not f.filename.endswith(".zip"):
        return _cors_response({"error": "must be .zip"}, 400)

    plugins_dir = _PLUGINS_DIR

    try:
        with tempfile.TemporaryDirectory() as tmp:
            zp = os.path.join(tmp, "plugin.zip")
            f.save(zp)
            with zipfile.ZipFile(zp, "r") as zf:
                members = zf.namelist()
                root_dirs = set(m.split("/")[0] for m in members if "/" in m and not m.split("/")[0].startswith("__"))
                if len(root_dirs) != 1:
                    return _cors_response({"error": "zip must contain single plugin folder"}, 400)
                plugin_name = list(root_dirs)[0]
                has_manifest = any(m == f"{plugin_name}/plugin.json" for m in members)
                if not has_manifest:
                    return _cors_response({"error": "missing plugin.json"}, 400)
                dest = os.path.join(plugins_dir, plugin_name)
                if os.path.exists(dest):
                    shutil.rmtree(dest)
                os.makedirs(dest, exist_ok=True)
                for m in members:
                    if m.endswith("/"):
                        os.makedirs(os.path.join(plugins_dir, m), exist_ok=True)
                        continue
                    target = os.path.join(plugins_dir, m)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(m) as src, open(target, "wb") as dst:
                        dst.write(src.read())

            return _cors_response({"status": "ok", "name": plugin_name})
    except Exception as e:
        return _cors_response({"error": str(e)}, 500)


@app.route("/api/plugins/repo-list", methods=["GET"])
def handle_repo_list():
    try:
        cfg = AppConfig()
        if not cfg.repo_url:
            return _cors_response({"error": "未配置插件仓库地址"}, 400)
        repo = PluginRepository(cfg.repo_url)
        plugins = asyncio.run(repo.fetch_index())
        return _cors_response({"plugins": plugins})
    except Exception as e:
        return _cors_response({"error": str(e)}, 500)


@app.route("/api/plugins/check-updates", methods=["GET"])
def handle_check_updates():
    try:
        cfg = AppConfig()
        if not cfg.repo_url:
            return _cors_response({"error": "未配置插件仓库地址"}, 400)
        repo = PluginRepository(cfg.repo_url)
        local = []
        plugins_dir = _PLUGINS_DIR
        for entry in os.listdir(plugins_dir):
            manifest = os.path.join(plugins_dir, entry, "plugin.json")
            if os.path.isfile(manifest):
                try:
                    with open(manifest, "r", encoding="utf-8") as f:
                        p = json.load(f)
                    p["id"] = p.get("id", entry)
                    local.append(p)
                except Exception:
                    pass
        result = asyncio.run(repo.check_updates(local))
        return _cors_response(result)
    except Exception as e:
        return _cors_response({"error": str(e)}, 500)


@app.route("/api/plugins/repo-install", methods=["POST"])
def handle_repo_install():
    try:
        cfg = AppConfig()
        if not cfg.repo_url:
            return _cors_response({"error": "未配置插件仓库地址"}, 400)
        params = request.get_json(force=True)
        download_url = params.get("download", "")
        if not download_url:
            return _cors_response({"error": "缺少download参数"}, 400)
        repo = PluginRepository(cfg.repo_url)
        plugins_dir = _PLUGINS_DIR
        name = asyncio.run(repo.install_plugin(download_url, plugins_dir))
        return _cors_response({"status": "ok", "name": name})
    except Exception as e:
        return _cors_response({"error": str(e)}, 500)


@app.route("/api/version", methods=["GET"])
def handle_version():
    from version import VERSION, GIT_REPO
    try:
        r = httpx.get(
            f"https://raw.githubusercontent.com/{GIT_REPO}/master/version.py",
            timeout=8, follow_redirects=True,
        )
        if r.status_code == 200:
            m = re.search(r'VERSION\s*=\s*"([^"]+)"', r.text)
            if m:
                latest = m.group(1)
                return _cors_response({
                    "current": VERSION,
                    "latest": latest,
                    "update": latest != VERSION,
                    "url": f"https://github.com/{GIT_REPO}/releases",
                })
    except Exception:
        pass
    return _cors_response({"current": VERSION, "latest": None, "update": None, "url": ""})


@app.route("/api/init", methods=["POST"])
def handle_init():
    try:
        params = request.get_json(force=True)
        url = params.get("url", "")
        if not url:
            return _cors_response({"error": "URL不能为空"}, 400)
        _apply_config(params)
        packer = NovelPacker.from_url(url)
        asyncio.run(packer.init())
        return _cors_response({
            "novel": {
                "title": packer.novel.title,
                "alias": packer.novel.alias,
                "author": packer.novel.author,
                "status": packer.novel.status,
                "coverUrl": packer.novel.cover_url,
                "tags": packer.novel.tags,
                "publisher": packer.novel.publisher,
                "description": packer.novel.description,
            },
            "catalog": {
                "volumes": [
                    {
                        "volumeName": v.volume_name,
                        "cover": v.cover,
                        "chapterCount": len(v.chapters),
                        "chapters": [
                            {"name": ch.chapter_name, "url": ch.chapter_url}
                            for ch in v.chapters
                        ],
                    }
                    for v in packer.catalog.volumes
                ],
            },
            "sourceName": packer.light_novel_source.name,
        })
    except Exception as e:
        return _cors_response({"error": str(e)}, 500)


@app.route("/api/pack", methods=["POST"])
def handle_pack():
    global _task_counter
    try:
        params = request.get_json(force=True)
        url = params.get("url", "")
        if not url:
            return _cors_response({"error": "URL不能为空"}, 400)
        _apply_config(params)

        task = TaskState(f"task_{_task_counter + 1}", "running")
        _task_counter += 1
        _tasks[task.id] = task

        selected_chapters = params.get("selectedChapters", [])
        combine_volume = params.get("combineVolume", False)
        add_chapter_title = params.get("addChapterTitle", True)
        output_format = params.get("outputFormat", "epub")

        task.add_log("info", "正在初始化...")

        threading.Thread(
            target=_run_pack_task,
            args=(task, url, params, selected_chapters, combine_volume, add_chapter_title, output_format),
            daemon=True
        ).start()

        return _cors_response({"taskId": task.id, "status": "started"})
    except Exception as e:
        return _cors_response({"error": str(e)}, 500)


@app.route("/api/stop", methods=["POST"])
def handle_stop():
    AppConfig().request_cancel()
    return _cors_response({"status": "cancelling"})


@app.route("/api/status", methods=["GET"])
def handle_status():
    task_id = request.args.get("task")
    if not task_id:
        return _cors_response({"error": "缺少task参数"}, 400)
    task = _tasks.get(task_id)
    if not task:
        return _cors_response({"error": "任务不存在"}, 404)
    return _cors_response({
        "status": task.status,
        "logs": task.logs,
        "outputFiles": task.output_files,
    })


@app.route("/api/download", methods=["GET"])
def handle_download():
    file_path = request.args.get("file", "")
    if not file_path or not os.path.exists(file_path):
        return _cors_response({"error": "文件不存在"}, 404)
    directory = os.path.dirname(file_path)
    filename = os.path.basename(file_path)
    return send_from_directory(
        directory, filename,
        mimetype="application/epub+zip",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/api/browse-files", methods=["GET"])
def handle_browse_files():
    try:
        out_dir = AppConfig().output_dir or "."
        if not os.path.isabs(out_dir):
            out_dir = os.path.join(_BASE_DIR, out_dir)
        out_dir = os.path.normpath(os.path.abspath(out_dir))
        files = []
        for root, dirs, filenames in os.walk(out_dir):
            rel_root = os.path.relpath(root, out_dir)
            if rel_root == ".":
                rel_root = ""
            for fn in filenames:
                if fn.lower().endswith((".epub", ".cbz")):
                    full = os.path.join(root, fn)
                    st = os.stat(full)
                    files.append({
                        "name": fn,
                        "path": full,
                        "dir": rel_root,
                        "size": st.st_size,
                        "mtime": st.st_mtime,
                    })
        files.sort(key=lambda f: f["mtime"], reverse=True)
        return _cors_response({"files": files})
    except Exception as e:
        return _cors_response({"error": str(e)}, 500)


@app.route("/api/open-file-location", methods=["POST"])
def handle_open_file_location():
    try:
        params = request.get_json(force=True)
        file_path = params.get("path", "")
        if not file_path or not os.path.exists(file_path):
            return _cors_response({"error": "文件不存在"}, 404)
        dir_path = os.path.dirname(os.path.abspath(file_path))
        os.startfile(dir_path)
        return _cors_response({"status": "ok"})
    except Exception as e:
        return _cors_response({"error": str(e)}, 500)


@app.route("/api/open-file", methods=["POST"])
def handle_open_file():
    try:
        params = request.get_json(force=True)
        file_path = params.get("path", "")
        if not file_path or not os.path.exists(file_path):
            return _cors_response({"error": "文件不存在"}, 404)
        os.startfile(os.path.abspath(file_path))
        return _cors_response({"status": "ok"})
    except Exception as e:
        return _cors_response({"error": str(e)}, 500)


@app.route("/api/browse-dir", methods=["POST"])
def handle_browse_dir():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title="选择保存目录")
        root.destroy()
        if path:
            return _cors_response({"path": path})
        return _cors_response({"path": None})
    except Exception as e:
        return _cors_response({"error": str(e)}, 500)


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_static(path):
    if not path:
        path = "index.html"
    static_dir = os.path.join(_BASE_DIR, "web")
    if os.path.exists(os.path.join(static_dir, path)):
        return send_from_directory(static_dir, path)
    return _cors_response({"error": "Not Found"}, 404)


def start_server(port=8080):
    url = f"http://localhost:{port}"

    try:
        import webview
        t = threading.Thread(target=app.run, kwargs={"host": "0.0.0.0", "port": port, "debug": False}, daemon=True)
        t.start()
        icon_path = os.path.join(_BASE_DIR, "web", "icon.ico")
        window = webview.create_window("ScrollPack", url, width=1100, height=720,
                                       min_size=(800, 500))
        webview.start()
    except ImportError:
        import webbrowser
        webbrowser.open(url)
        print(f"WebUI: {url}")
        app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    port = 20250
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            pass
    start_server(port=port)
