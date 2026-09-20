"""Isolated browser check with deterministic model responses; never consumes a subscription."""

import socket
import re
import json
import shutil
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright, expect

from dnd_helper.free_world import DirectorCard
from dnd_helper.web import create_app


class Stub:
    def status(self):
        return {"ready": True, "message": "Тестовый вход через ChatGPT"}

    def close(self):
        pass

    def structured(self, instruction, payload, schema, cancel, model=""):
        if schema is DirectorCard:
            hint = payload["action"].get("mode") == "hint"
            actor = payload["action"]["actor"]
            risky = "колодец" in payload["action"]["text"]
            directed = payload.get("director_note", "")
            value = DirectorCard.model_validate(
                dict(
                    summary="Нужна проверка ловкости" if risky else "Указание ведущего учтено" if directed else "Мира берёт ведро и обещает вернуться.",
                    gm_hint="Предложите игроку описать намерение. Не решайте за него.",
                    intent={"kind": "other", "goal": "Разрешить заявку", "targets": []},
                    stop="advice" if hint else "check" if risky else "completed",
                    progress="meaningful",
                    evidence=["water"],
                    question=None,
                    speaker=None,
                    check={"stat": "agility", "difficulty": "standard"} if risky and not hint else None,
                    success={
                        "read_aloud": "У колодца особенно заметно пустое ведро — именно так, как указал ведущий."
                        if directed
                        else "Ада придерживает ведро, пока Мира перехватывает ручку поудобнее. «Спасибо. Хоть знать буду, что я тут не одна с этой бедой», — говорит она и отступает от колодца, освобождая дорогу.",
                        "summary": "Переход завершён" if risky else "Ведро выведено в сцену" if directed else "Ведро у Миры; обещание записано.",
                        "effects": []
                        if risky or hint
                        else [{"effect": "present", "entity": "bucket"}]
                        if directed
                        else [
                            {"effect": "transfer", "item": "bucket", "destination": actor},
                            {
                                "effect": "remember",
                                "text": "Мира обещала вернуться.",
                                "status": "promise",
                                "visibility": "public",
                                "known_by": ["ada", actor],
                                "subjects": ["ada", "bucket"],
                            },
                        ],
                    },
                    failure={
                        "read_aloud": "Герой ушибся, но может продолжать.",
                        "summary": "Мира ушиблась: −2 HP.",
                        "effects": [{"effect": "harm", "entity": actor}],
                    }
                    if risky and not hint
                    else None,
                )
            )
        else:
            raise AssertionError("Only one assistant call is expected")
        return value, {
            "seconds": 0.1,
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }


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
                browser = p.chromium.launch(
                    executable_path=shutil.which("google-chrome") or shutil.which("chromium")
                )
                page = browser.new_page(viewport={"width": 1366, "height": 900})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(f"http://127.0.0.1:{port}/world")
                page.locator("#open-settings").click()
                expect(page.locator("#connection-status")).to_contain_text("ChatGPT")
                with page.expect_response(lambda response: response.url.endswith("/api/world/model")):
                    page.locator("#world-model").select_option("gpt-5.6-terra")
                page.reload()
                page.locator("#open-settings").click()
                expect(page.locator("#world-model")).to_have_value("gpt-5.6-terra")
                page.get_by_label("Закрыть настройки", exact=True).click()
                page.locator("#start-example").click()
                expect(page.locator("#connection-status")).not_to_be_visible()
                expect(page.locator("#documents")).not_to_be_visible()
                expect(page.locator("#reading")).to_contain_text("Я Ада")
                assert (
                    page.locator("#reading .read-aloud").evaluate(
                        "el => parseFloat(getComputedStyle(el).fontSize)"
                    )
                    >= 22
                )
                page.locator("#open-lore").click()
                expect(page.locator("#lore-content")).to_contain_text("Берёзовый Брод")
                expect(page.locator("#lore-content")).to_contain_text("ветка заклинила")
                page.get_by_label("Закрыть описание мира").click()
                expect(page.locator("#participants")).to_contain_text("Торвин")
                page.locator('#participants input[value="torvin"]').check()
                Path("/tmp/dnd-world-browser").mkdir(exist_ok=True)
                page.screenshot(path="/tmp/dnd-world-browser/initial.png", full_page=True)
                page.locator("#action").fill("Беру ведро и обещаю вернуться.")
                page.locator("#send").click()
                expect(page.get_by_role("button", name="Применить и продолжить")).to_be_visible()
                expect(page.locator("#facts")).not_to_contain_text("Мира обещала вернуться.")
                expect(page.locator("#world-model")).to_be_disabled()
                expect(page.locator("#review-effects")).not_to_have_attribute("open", "")
                expect(page.locator("#reading")).to_contain_text("Ада придерживает ведро")
                page.screenshot(path="/tmp/dnd-world-browser/card.png", full_page=True)
                page.reload()
                page.get_by_role("button", name="Применить и продолжить").click()
                expect(page.locator("#history")).to_contain_text("Мира + Торвин")
                expect(page.locator("#facts")).to_contain_text("Мира обещала вернуться.")
                expect(page.locator("#world")).to_contain_text("Перевязь, Ведро")
                page.locator("#action").fill("Спускаюсь в колодец.")
                page.locator("#send").click()
                expect(page.get_by_role("button", name="Ввести бросок")).to_be_visible()
                page.get_by_label("Результат физического кубика").fill("2")
                page.get_by_role("button", name="Ввести бросок").click()
                page.get_by_role("button", name="Применить и продолжить").click()
                expect(page.locator("#world")).to_contain_text("6/8 здоровья")
                page.locator("#undo").click()
                expect(page.locator("#world")).to_contain_text("8/8 здоровья")
                expect(page.locator("#facts")).to_contain_text("Мира обещала вернуться.")
                # A known move is prepared without consulting the stub/model.
                count = len(app.state.service.free_world.store.read()["calls"])
                page.get_by_role("button", name="Мира: перейти — Лесная тропа").click()
                page.get_by_role("button", name="Применить и продолжить").click()
                expect(page.locator("#world")).to_contain_text("Лесная тропа")
                assert len(app.state.service.free_world.store.read()["calls"]) == count
                page.locator("#undo").click()
                # File import, repair guidance, four switchable heroes and a GM question.
                page.locator("#open-settings").click()
                page.locator("#documents > summary").click()
                page.locator("#document-text").fill("{}")
                page.locator("#validate-document").click()
                expect(page.locator("#document-report")).to_contain_text("Ошибки")
                page.locator("#example-document").click()
                expect(page.locator("#document-text")).to_have_value(re.compile("dnd-world@1"))
                # Four heroes must remain one visible hero card, not four stacked panels.
                raw = json.loads(page.locator("#document-text").input_value())
                base = next(e for e in raw["entities"] if e["id"] == "mira")
                raw["entities"].extend(
                    [{**base, "id": "lea", "name": "Лея"}, {**base, "id": "jan", "name": "Ян"}]
                )
                page.locator("#document-text").fill(json.dumps(raw, ensure_ascii=False))
                page.locator("#validate-document").click()
                expect(page.locator("#import-document")).to_be_visible()
                page.on("dialog", lambda dialog: dialog.accept())
                page.locator("#import-document").click()
                expect(page.get_by_role("tab")).to_have_count(4)
                expect(page.locator("#world .hero-name")).to_have_count(1)
                page.get_by_role("tab", name="Торвин", exact=True).click()
                expect(page.locator("#actor")).to_have_value("torvin")
                expect(page.locator("#world")).to_contain_text("Торвин")
                page.locator("#ask-hint").click()
                expect(page.locator("#mode-note")).to_contain_text("останутся на месте")
                page.locator("#action").fill("Игроки растерялись. Что предложить ведущему?")
                page.locator("#send").click()
                expect(page.get_by_role("button", name="Понятно · закрыть совет")).to_be_visible()
                page.get_by_role("button", name="Исправить текст", exact=True).click()
                page.locator("#edit-text").fill("Ада ждёт вашего решения.")
                page.locator("#edit-summary").fill("Ведущий предложил два подхода.")
                page.get_by_role("button", name="Сохранить правки").click()
                expect(page.locator("#pending")).to_contain_text("Правки ведущего сохранены")
                page.reload()
                expect(page.locator("#pending")).to_contain_text("Ада ждёт вашего решения.")
                page.get_by_role("button", name="Понятно · закрыть совет").click()
                expect(page.locator("#history")).to_contain_text("Вопрос ведущего")
                expect(page.locator("#action-title")).to_have_text("Что делают игроки?")
                expect(page.locator("#send")).to_have_text("Разобрать действие")
                assert app.state.service.free_world.store.read()["entities"]["torvin"]["location"] == "square"
                page.locator("#direct-model").click()
                expect(page.locator("#mode-note")).to_contain_text("обязательным")
                page.locator("#action").fill("Покажи существующее ведро, но ничего не придумывай внутри него.")
                page.locator("#send").click()
                expect(page.locator("#director-note")).to_contain_text("Покажи существующее ведро")
                page.locator("#action").fill("Торвин осматривается у колодца.")
                page.locator("#send").click()
                expect(page.locator("#reading")).to_contain_text("пустое ведро")
                expect(page.locator("#review-effects")).to_contain_text("Выведено в сцену")
                page.get_by_role("button", name="Поправить помощника").click()
                page.locator("#redirect-text").fill("Сделай упоминание ведра коротким и явным.")
                page.get_by_role("button", name="Пересобрать карточку").click()
                expect(page.locator("#reading")).to_contain_text("пустое ведро")
                page.get_by_role("button", name="Применить и продолжить").click()
                expect(page.locator("#director-note")).to_be_hidden()
                page.locator("#open-settings").click()
                page.locator("#documents > summary").click()
                with page.expect_download() as download_info:
                    page.locator("#export-log").click()
                journal_path = Path(folder) / "journal.json"
                download_info.value.save_as(journal_path)
                journal = json.loads(journal_path.read_text("utf-8"))
                assert journal["format"] == "dnd-world-log@1"
                assert len(journal["events"]) == 2
                assert journal["events"][0]["edited"]
                page.locator("#document-file").set_input_files(journal_path)
                expect(page.locator("#document-text")).to_have_value(re.compile("dnd-world-log@1"))
                page.locator("#validate-document").click()
                expect(page.locator("#document-report")).to_contain_text("Записей журнала: 2")
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
                    "Browser: readable speech, hidden setup, lore, four hero tabs, advice mode reset, dice, editing, journals, mobile OK"
                )
        finally:
            app.state.service.free_world.close()
            server.should_exit = True
            thread.join(10)
            sock.close()


if __name__ == "__main__":
    main()
