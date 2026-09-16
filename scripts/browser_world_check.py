"""Isolated browser check with deterministic model responses; never consumes a subscription."""

import socket
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright, expect

from dnd_helper.free_world import Proposal, Verdict, Story
from dnd_helper.web import create_app


class Stub:
    def status(self):
        return {"ready": True, "message": "Тестовый вход через ChatGPT"}

    def close(self):
        pass

    def structured(self, instruction, payload, schema, cancel, model=""):
        if schema is Proposal:
            risky = "колодец" in payload["action"]["text"]
            value = Proposal.model_validate(
                dict(
                    summary="Нужна проверка ловкости" if risky else "Мира берёт ведро и обещает вернуться.",
                    evidence=["water"],
                    question=None,
                    speaker=None,
                    check={"stat": "agility", "difficulty": "standard"} if risky else None,
                    success={
                        "summary": "Переход завершён" if risky else "Ведро у Миры; обещание записано.",
                        "operations": []
                        if risky
                        else [
                            {"op": "transfer", "item": "bucket", "before": "square", "destination": "mira"},
                            {
                                "op": "remember",
                                "id": "promise",
                                "text": "Мира обещала вернуться.",
                                "status": "promise",
                                "visibility": "public",
                                "known_by": ["ada", "mira"],
                            },
                        ],
                    },
                    failure={
                        "summary": "Мира ушиблась: −2 HP.",
                        "operations": [{"op": "damage", "entity": "mira"}],
                    }
                    if risky
                    else None,
                )
            )
        elif schema is Verdict:
            value = Verdict(accepted=True, reason="Согласовано")
        else:
            value = Story(
                text="Мира берёт ведро." if not payload.get("roll") else "Мира ушиблась, но может продолжать."
            )
        return value, {"seconds": 0.1, "usage": {"input_tokens": 10, "output_tokens": 10}}


def main():
    with tempfile.TemporaryDirectory(prefix="dnd-world-browser-") as folder:
        app = create_app(Path(folder), background=False)
        app.state.service.free_world.provider = Stub()
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
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page(viewport={"width": 1366, "height": 900})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(f"http://127.0.0.1:{port}/world")
                expect(page.locator("#connection-status")).to_contain_text("ChatGPT")
                page.locator("#new").click()
                page.locator("#action").fill("Беру ведро и обещаю вернуться.")
                page.locator("#send").click()
                expect(page.get_by_role("button", name="Принять исход")).to_be_visible()
                expect(page.locator("#facts")).not_to_contain_text("Мира обещала вернуться.")
                page.reload()
                page.get_by_role("button", name="Принять исход").click()
                expect(page.locator("#facts")).to_contain_text("Мира обещала вернуться.")
                expect(page.locator("#world")).to_contain_text("Перевязь, Ведро")
                page.locator("#action").fill("Спускаюсь в колодец.")
                page.locator("#send").click()
                expect(page.get_by_role("button", name="Ввести бросок")).to_be_visible()
                page.get_by_label("Результат физического кубика").fill("2")
                page.get_by_role("button", name="Ввести бросок").click()
                page.get_by_role("button", name="Принять исход").click()
                expect(page.locator("#world")).to_contain_text("6/8 HP")
                page.locator("#undo").click()
                expect(page.locator("#world")).to_contain_text("8/8 HP")
                expect(page.locator("#facts")).to_contain_text("Мира обещала вернуться.")
                Path("/tmp/dnd-world-browser").mkdir(exist_ok=True)
                page.screenshot(path="/tmp/dnd-world-browser/desktop.png", full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path="/tmp/dnd-world-browser/mobile.png", full_page=True)
                assert not errors, errors
                browser.close()
                print("Browser: proposal, reload, acceptance, check, failure, undo, mobile OK")
        finally:
            app.state.service.free_world.close()
            server.should_exit = True
            thread.join(10)
            sock.close()


if __name__ == "__main__":
    main()
