"""Start a real source/frozen app, play one step, reopen it and verify persistence."""

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
    with tempfile.TemporaryDirectory(prefix="dnd-smoke-") as temp:
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
                            with urllib.request.urlopen(candidate + "/health", timeout=1) as r:
                                if json.load(r)["ok"]:
                                    url = candidate
                                    break
                        except (OSError, ValueError):
                            pass
                    time.sleep(0.1)
                assert url, "Application did not start"
                with urllib.request.urlopen(url) as r:
                    html = r.read().decode()
                token = re.search(r'name="dnd-token" content="([^"]+)"', html)[1]

                def request(path, data=None):
                    payload = None if data is None else json.dumps(data).encode()
                    req = urllib.request.Request(
                        url + "/api/" + path,
                        data=payload,
                        headers={"X-Dnd-Token": token, "Content-Type": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=5) as r:
                        return json.load(r)

                def command(kind, data=None):
                    return request(
                        "command", {"kind": kind, "data": data or {}, "command_id": f"smoke-{kind}"}
                    )

                if iteration == 0:
                    command("new", {"characters": ["mira"], "demo": True})
                    command("start")
                    command("submit", {"actor": "mira", "intent": "read", "text": "Читаю табличку"})
                    state = command("accept")["game"]
                    assert state["clues"] == ["manual"]
                    expected_id = state["id"]
                    second = subprocess.run(
                        [*executable, "--no-browser", "--data-dir", str(directory)], timeout=10
                    )
                    assert second.returncode == 0, "Second instance should exit without a second bot"
                else:
                    state = request("state")["game"]
                    assert state["id"] == expected_id and state["clues"] == ["manual"]
                # Verify authoring resources and import in the actual source/frozen server.
                kit = request("author-kit")
                assert "JSON SCHEMA" in kit["prompt"]
                report = request("adventures/import", {"text": json.dumps(kit["example"])})
                assert report["ok"] and report["key"]
                if iteration == 1:
                    state = request(
                        "command",
                        {
                            "kind": "new",
                            "data": {"adventure_key": report["key"], "characters": ["lea"]},
                            "command_id": "smoke-imported-new",
                        },
                    )["game"]
                    assert state["definition"]["title"] == "Последний сигнал"
                    request("command", {"kind": "start", "data": {}, "command_id": "smoke-imported-start"})
                    request(
                        "command",
                        {
                            "kind": "submit",
                            "data": {"actor": "lea", "intent": "evacuate", "text": "Эвакуация"},
                            "command_id": "smoke-imported-action",
                        },
                    )
                    final = request(
                        "command", {"kind": "accept", "data": {}, "command_id": "smoke-imported-accept"}
                    )
                    assert final["presentation"]["can_finish"]
                request("shutdown", {})
                process.wait(timeout=35)
                assert process.returncode == 0
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                process.stderr.close()
    print(
        "Smoke passed: real HTTP, Unicode path, game step, single instance, restart, shutdown, bundled author kit, imported adventure."
    )


if __name__ == "__main__":
    main()
