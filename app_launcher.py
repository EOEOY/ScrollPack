"""
ScrollPack desktop launcher - PyWebView native window
"""
import os
import sys
import time
import socket
import threading


def get_base_paths():
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
        exe_dir = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        exe_dir = base

    return {
        'base': base,
        'exe_dir': exe_dir,
        'plugins': os.path.join(exe_dir, 'plugins'),
        'web': os.path.join(base, 'web'),
    }


def wait_for_server(port, timeout=10):
    start = time.time()
    while time.time() - start < timeout:
        try:
            s = socket.create_connection(('127.0.0.1', port), timeout=1)
            s.close()
            return True
        except (socket.error, OSError):
            time.sleep(0.1)
    return False


def main():
    paths = get_base_paths()
    os.environ['SCROLLPACK_BASE_DIR'] = paths['base']
    sys.path.insert(0, paths['base'])
    os.makedirs(paths['plugins'], exist_ok=True)

    port = 20250
    url = f"http://127.0.0.1:{port}"

    from web.server import app

    def run_flask():
        app.run(host='127.0.0.1', port=port, debug=False)

    t = threading.Thread(target=run_flask, daemon=True)
    t.start()

    if not wait_for_server(port):
        print("Server failed to start")
        return

    try:
        import webview
        webview.create_window("ScrollPack", url, width=1100, height=720,
                              min_size=(500, 500), confirm_close=True)
        webview.start(gui='edgechromium', debug=False)
    except ImportError:
        import webbrowser
        webbrowser.open(url)
        print(f"Open {url}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
