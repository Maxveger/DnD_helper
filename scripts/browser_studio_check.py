"""Isolated browser check for /studio; deterministic responses, no subscription use."""

import shutil
import socket
import tempfile
import threading
import time
from copy import deepcopy
from pathlib import Path

import uvicorn
from playwright.sync_api import expect, sync_playwright

from dnd_helper.studio import StudioCard, StudioTurn, load_dossier
from dnd_helper.web import create_app


class Stub:
    def __init__(self):
        self.calls = []

    def status(self):
        return {"ready": True, "message": "Тестовый вход через ChatGPT"}

    def close(self):
        pass

    def conversation(self, prompt, cancel, model="", session_id=None, schema_class=None):
        self.calls.append(
            {"prompt": prompt, "session_id": session_id, "schema_class": schema_class}
        )
        if "\nСовет:" in prompt:
            turn = StudioTurn(
                kind="chat",
                card=None,
                answer="Да. Первая половина платы уже лежит у Иво; кошель на столе — вторая.",
                observations=["Совет не меняет принятую память."],
            )
        else:
            capsule = deepcopy(load_dossier()["initial_capsule"])
            capsule["known"].append("Иво запомнил общий план Дома")
            turn = StudioTurn(
                kind="card",
                card=StudioCard.model_validate(
                    {
                        "interpretation": "Иво просит показать карту.",
                        "gm_note": "Сайрус показывает общий план без секретов Ордена.",
                        "stop": "resolved",
                        "canon_used": ["У Сайруса есть грубый план Дома."],
                        "outcome": {
                            "read_aloud": "Сайрус поворачивает лист. На нём отмечены вход, канцелярия, архив и решётка реликвария.",
                            "summary": "Иво изучил общий план Дома.",
                            "capsule_after": capsule,
                            "introduced_details": [],
                        },
                        "check": None,
                    }
                ),
                answer=None,
                observations=[],
            )
        return turn.model_dump_json(), {
            "seconds": 0.1,
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "session_id": session_id or "11111111-1111-1111-1111-111111111111",
        }


def main():
    with tempfile.TemporaryDirectory(prefix="dnd-studio-browser-") as folder:
        app = create_app(Path(folder), background=False)
        stub = Stub()
        app.state.service.free_world.provider = stub
        app.state.service.studio.provider = stub
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                assert time.monotonic() < deadline
                time.sleep(0.05)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=shutil.which("google-chrome") or shutil.which("chromium")
                )
                page = browser.new_page(viewport={"width": 1366, "height": 768})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{port}/studio")
                expect(page.locator("#welcome-connection")).to_contain_text("подключена")
                page.locator("#start").click()
                expect(page.locator("#transcript")).to_contain_text("Сайрус Вейн")
                expect(page.locator("#model-memory-status")).to_contain_text("начнётся")

                page.locator("#action").fill("Позволь взглянуть на карту")
                page.locator("#send-action").click()
                expect(page.get_by_role("button", name="Принять текст и память")).to_be_visible()
                expect(page.locator("#capsule")).not_to_contain_text("Иво запомнил общий план")
                assert stub.calls[0]["session_id"] is None
                assert '"id": "eye_studio_slice"' in stub.calls[0]["prompt"]
                page.get_by_role("button", name="Принять текст и память").click()
                expect(page.locator("#capsule")).to_contain_text("Иво запомнил общий план")
                expect(page.locator("#model-memory-status")).to_contain_text("1 ответов")
                assert page.locator("#game-stream").evaluate(
                    "el => getComputedStyle(el).overflowY === 'auto' && el.scrollHeight > el.clientHeight"
                )
                assert page.locator("aside").evaluate(
                    "el => getComputedStyle(el).overflowY === 'auto'"
                )
                assert page.evaluate("document.body.scrollHeight <= innerHeight + 1")
                expect(page.locator("#action")).to_be_in_viewport()

                page.locator("#private-input").fill("Сайрус уже дал задаток?")
                page.locator("#send-private").click()
                expect(page.locator("#private-history")).to_contain_text("Первая половина платы")
                assert stub.calls[-1]["session_id"] == "11111111-1111-1111-1111-111111111111"
                assert '"id": "eye_studio_slice"' not in stub.calls[-1]["prompt"]

                page.locator("#remind-dossier").click()
                expect(page.locator("#model-memory-status")).to_contain_text("Досье будет приложено")
                page.on("dialog", lambda dialog: dialog.accept())
                page.locator("#reset-model").click()
                expect(page.locator("#model-memory-status")).to_contain_text("начнётся")

                output = Path("/tmp/dnd-studio-browser")
                output.mkdir(exist_ok=True)
                page.screenshot(path=output / "desktop.png", full_page=True)
                assert not errors, errors
                browser.close()
                print("Studio browser: fixed laptop workspace and independent panes OK")
        finally:
            app.state.service.studio.close()
            app.state.service.free_world.close()
            server.should_exit = True
            thread.join(10)
            sock.close()


if __name__ == "__main__":
    main()
