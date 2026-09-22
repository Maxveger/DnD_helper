"""Start the real app twice and verify studio persistence without calling a model."""

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path)
    args = parser.parse_args()
    executable = [str(args.executable.resolve())] if args.executable else [sys.executable, "-m", "dnd_helper"]
    with tempfile.TemporaryDirectory(prefix="dnd-studio-smoke-") as temp:
        directory = Path(temp) / "Игра ведущего"
        directory.mkdir()
        expected_id = None
        for iteration in range(2):
            process = subprocess.Popen(
                [*executable, "--no-browser", "--port", "0", "--data-dir", str(directory)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            try:
                url = None
                for _ in range(200):
                    if process.poll() is not None:
                        raise RuntimeError(process.stderr.read().decode(errors="replace"))
                    runtime = directory / "runtime.json"
                    if runtime.exists():
                        try:
                            port = json.loads(runtime.read_text("utf-8"))["port"]
                            candidate = f"http://127.0.0.1:{port}"
                            with urllib.request.urlopen(candidate + "/health", timeout=1) as response:
                                if json.load(response)["ok"]:
                                    url = candidate
                                    break
                        except (OSError, ValueError):
                            pass
                    time.sleep(0.1)
                assert url, "Application did not start"
                with urllib.request.urlopen(url) as response:
                    html = response.read().decode()
                assert "Игровая IDE" in html and "Сердце водокачки" not in html
                token = re.search(r'name="dnd-token" content="([^"]+)"', html)[1]

                def request(path, data=None):
                    payload = None if data is None else json.dumps(data).encode()
                    req = urllib.request.Request(
                        url + "/api/" + path,
                        data=payload,
                        headers={"X-Dnd-Token": token, "Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=5) as response:
                        return json.load(response)

                if iteration == 0:
                    state = request(
                        "studio/command",
                        {"kind": "new", "data": {}, "command_id": "smoke-new", "revision": None},
                    )["studio"]
                    expected_id = state["id"]
                    second = subprocess.run(
                        [*executable, "--no-browser", "--data-dir", str(directory)], timeout=10
                    )
                    assert second.returncode == 0, "Second instance should exit"
                else:
                    state = request("studio")["studio"]
                    assert state["id"] == expected_id
                request("shutdown", {})
                process.wait(timeout=35)
                assert process.returncode == 0
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                process.stderr.close()
    print("Smoke passed: studio-only HTTP, Unicode path, persistence, single instance and shutdown.")


if __name__ == "__main__":
    main()
