"""Isolated browser check with deterministic model responses; never consumes a subscription."""

import socket
import json
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright, expect

from dnd_helper.free_world import Advice
from dnd_helper.web import create_app


class Stub:
    def status(self):
        return {"ready": True, "message": "Тестовый вход через ChatGPT"}

    def close(self):
        pass

    def structured(self, instruction, payload, schema, cancel, model=""):
        if schema is Advice:
            hint = payload["action"].get("mode") == "hint"
            actor = payload["action"]["actor"]
            risky = "колодец" in payload["action"]["text"]
            value = Advice.model_validate(
                dict(
                    summary="Нужна проверка ловкости" if risky else "Мира берёт ведро и обещает вернуться.",
                    gm_hint="Предложите игроку описать намерение. Не решайте за него.",
                    evidence=["water"],
                    question=None,
                    speaker=None,
                    check={"stat": "agility", "difficulty": "standard"} if risky else None,
                    success={
                        "read_aloud": "Черновик реплики для ведущего.",
                        "summary": "Переход завершён" if risky else "Ведро у Миры; обещание записано.",
                        "operations": []
                        if risky or hint
                        else [
                            {"op": "transfer", "item": "bucket", "before": "square", "destination": actor},
                            {
                                "op": "remember",
                                "id": "promise",
                                "text": "Мира обещала вернуться.",
                                "status": "promise",
                                "visibility": "public",
                                "known_by": ["ada", actor],
                            },
                        ],
                    },
                    failure={
                        "read_aloud": "Герой ушибся, но может продолжать.",
                        "summary": "Мира ушиблась: −2 HP.",
                        "operations": [{"op": "damage", "entity": actor}],
                    }
                    if risky
                    else None,
                )
            )
        else:
            raise AssertionError("Only one assistant call is expected")
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
                with page.expect_response(lambda response: response.url.endswith("/api/world/model")):
                    page.locator("#world-model").select_option("gpt-5.6-terra")
                page.reload()
                expect(page.locator("#world-model")).to_have_value("gpt-5.6-terra")
                page.locator("#new").click()
                page.locator("#action").fill("Беру ведро и обещаю вернуться.")
                page.locator("#send").click()
                expect(page.get_by_role("button", name="Принять исход")).to_be_visible()
                expect(page.locator("#facts")).not_to_contain_text("Мира обещала вернуться.")
                expect(page.locator("#world-model")).to_be_disabled()
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
                # A known move is prepared without consulting the stub/model.
                count = len(app.state.service.free_world.store.read()["calls"])
                page.get_by_role("button", name="Перейти: Лесная тропа").click()
                page.get_by_role("button", name="Принять исход").click()
                expect(page.locator("#world")).to_contain_text("Лесная тропа")
                assert len(app.state.service.free_world.store.read()["calls"]) == count
                page.locator("#undo").click()
                # File import, repair guidance, two heroes and a GM question.
                page.locator("#documents > summary").click()
                page.locator("#document-text").fill("{}")
                page.locator("#validate-document").click()
                expect(page.locator("#document-report")).to_contain_text("Ошибки")
                page.locator("#example-document").click()
                page.wait_for_function(
                    "document.getElementById('document-text').value.includes('dnd-world@1')"
                )
                page.locator("#validate-document").click()
                expect(page.locator("#import-document")).to_be_visible()
                page.on("dialog", lambda dialog: dialog.accept())
                page.locator("#import-document").click()
                expect(page.locator("#world")).to_contain_text("Торвин")
                page.locator("#actor").select_option("torvin")
                page.locator("#request-mode").select_option("hint")
                page.locator("#action").fill("Игроки растерялись. Что предложить ведущему?")
                page.locator("#send").click()
                expect(page.get_by_role("button", name="Принять исход")).to_be_visible()
                page.locator("#pending details").last.locator("summary").click()
                page.get_by_label("Исправить реплику").fill("Ада ждёт вашего решения.")
                page.get_by_label("Исправить итог").fill("Ведущий предложил два подхода.")
                page.get_by_role("button", name="Сохранить правки").click()
                expect(page.locator("#pending")).to_contain_text("Правки ведущего сохранены")
                page.reload()
                expect(page.locator("#pending")).to_contain_text("Ада ждёт вашего решения.")
                page.get_by_role("button", name="Принять исход").click()
                expect(page.locator("#history")).to_contain_text("Вопрос ведущего")
                page.locator("#documents > summary").click()
                with page.expect_download() as download_info:
                    page.locator("#export-log").click()
                journal_path = Path(folder) / "journal.json"
                download_info.value.save_as(journal_path)
                journal = json.loads(journal_path.read_text("utf-8"))
                assert journal["format"] == "dnd-world-log@1"
                assert journal["events"][0]["edited"]
                page.locator("#document-file").set_input_files(journal_path)
                page.wait_for_function(
                    "document.getElementById('document-text').value.includes('dnd-world-log@1')"
                )
                page.locator("#validate-document").click()
                expect(page.locator("#document-report")).to_contain_text("Записей журнала: 1")
                page.locator("#import-document").click()
                expect(page.locator("#history")).to_contain_text("Ада ждёт вашего решения.")
                Path("/tmp/dnd-world-browser").mkdir(exist_ok=True)
                page.screenshot(path="/tmp/dnd-world-browser/desktop.png", full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path="/tmp/dnd-world-browser/mobile.png", full_page=True)
                assert not errors, errors
                browser.close()
                print(
                    "Browser: GM advice, edit, reload, dice, undo, local travel, custom scenario, journal roundtrip, mobile OK"
                )
        finally:
            app.state.service.free_world.close()
            server.should_exit = True
            thread.join(10)
            sock.close()


if __name__ == "__main__":
    main()
