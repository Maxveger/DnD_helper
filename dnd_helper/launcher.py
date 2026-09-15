"""One process for the web, bot and database; works from source and PyInstaller."""

import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from .config import data_directory


def console_message(text, stream):
    if stream is not None:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        stream.write(text.encode(encoding, errors="backslashreplace").decode(encoding) + "\n")
        stream.flush()


class InstanceLock:
    def __init__(self, directory):
        self.file = (directory / "instance.lock").open("a+b")
        # Windows byte-range locks prohibit reading the locked byte too.
        # File metadata is safe to inspect before attempting to acquire the lock.
        if os.fstat(self.file.fileno()).st_size == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)

    def acquire(self):
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def close(self):
        self.file.close()


def _main():
    parser = argparse.ArgumentParser(description="DnD Helper — локальный помощник ведущего")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    folder = args.data_dir or data_directory()
    folder.mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(folder)
    runtime = folder / "runtime.json"
    if not lock.acquire():
        if runtime.exists() and not args.no_browser:
            info = json.loads(runtime.read_text("utf-8"))
            webbrowser.open(f"http://127.0.0.1:{int(info['port'])}")
        lock.close()
        return
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        try:
            sock.bind(("127.0.0.1", args.port))
        except OSError:
            sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        runtime.write_text(json.dumps({"port": port}), "utf-8")
        import uvicorn
        from .web import create_app

        server = None

        def shutdown():
            if server:
                server.should_exit = True

        app = create_app(folder, shutdown=shutdown)
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False)
        )
        url = f"http://127.0.0.1:{port}"

        def open_when_ready():
            for _ in range(100):
                try:
                    with urllib.request.urlopen(url + "/health", timeout=1) as response:
                        if response.status == 200:
                            webbrowser.open(url)
                            return
                except OSError:
                    time.sleep(0.1)

        if not args.no_browser:
            threading.Thread(target=open_when_ready, daemon=True).start()
        console_message(f"DnD Helper: {url}\nДанные: {folder}", sys.stdout)
        server.run(sockets=[sock])
    finally:
        sock.close()
        runtime.unlink(missing_ok=True)
        lock.close()


def main():
    try:
        _main()
    except Exception as exc:
        message = (
            "Не удалось запустить DnD Helper. Проверьте доступ к папке данных и файл settings.local.json. "
            f"Тип ошибки: {type(exc).__name__}. Подробности ключей не выводятся."
        )
        if os.name == "nt" and "--no-browser" not in sys.argv:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "DnD Helper", 0x10)
        else:
            console_message(message, sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
