"""UI acceptance: author kit, invalid import, custom d6 story, reload and secrets.

Uses an isolated app by default; --executable tests the frozen package. --url is
for a separately running disposable test instance (the script creates a game).
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def exercise(url, output):
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url)
        page.locator("[data-tab=library]").click()
        expect(page.locator("#library-view")).to_be_visible()
        with page.expect_download() as download:
            page.locator("#download-author-kit").click()
        content = Path(download.value.path()).read_text("utf-8")
        assert "JSON SCHEMA" in content and "declarative@1" in content
        page.locator("#adventure-document").fill('{"format":"broken"}')
        page.locator("#validate-adventure").click()
        expect(page.locator("#import-report")).to_contain_text("Нужно исправить")
        expect(page.locator("#copy-repair")).to_be_visible()
        expect(page.locator("#import-adventure")).to_be_disabled()
        page.locator("#load-example").click()
        expect(page.locator("#adventure-document")).to_contain_text("")  # Wait on populated value below.
        expect(page.locator("#adventure-document")).to_have_value(re.compile(".*Последний сигнал.*", re.S))
        # Exercise file input too, using precisely the bundled example from the running app.
        document = page.locator("#adventure-document").input_value()
        page.locator("#adventure-file").set_input_files(
            {"name": "история.json", "mimeType": "application/json", "buffer": document.encode()}
        )
        page.locator("#validate-adventure").click()
        expect(page.locator("#import-report")).to_contain_text("Документ готов к импорту")
        expect(page.locator("#import-adventure")).to_be_enabled()
        page.screenshot(path=str(output / "import.png"), full_page=True)
        page.locator("#import-adventure").click()
        item = page.locator("#adventure-library article").filter(has_text="Последний сигнал")
        expect(item).to_be_visible()
        item.get_by_role("button", name="Играть", exact=True).click()
        expect(page.locator("#character-options")).to_contain_text("Лея")
        page.locator("#new-form button[type=submit]").click()
        expect(page.locator("#new-dialog")).not_to_be_visible()
        page.locator("#primary-action").click()
        expect(page.locator("#adventure-title")).to_have_text("Последний сигнал")
        expect(page.locator("#party")).to_contain_text("Техника")

        def prepared(intent):
            page.locator(f"[data-intent={intent}]").click()
            expect(page.locator("#primary-action")).to_have_text("Продолжить →")
            page.locator("#primary-action").click()
            expect(page.locator("#primary-action")).not_to_have_text("Продолжить →")

        prepared("read_log")
        expect(page.locator("[data-intent=read_log]")).to_have_count(0)
        expect(page.locator("#clues")).to_contain_text("314")
        page.locator("#quick-actions [data-scene=relay]").click()
        expect(page.locator("#scene-number")).to_contain_text("Ретранслятор")
        page.locator("[data-intent=align]").click()
        expect(page.locator("#primary-action")).to_contain_text("Попросить бросок: Лея")
        page.locator("#primary-action").click()
        page.reload()  # Outstanding request survives a browser restart.
        page.locator("#open-roll").click()
        expect(page.locator("#digital-roll")).to_have_text("Бросить d6")
        expect(page.locator("#physical-die")).to_have_attribute("max", "6")
        page.locator("#physical-die").fill("1")
        page.locator("#roll-form button[type=submit]").click()
        expect(page.locator("#roll-panel")).to_contain_text("Проверка не пройдена")
        page.locator("#primary-action").click()
        expect(page.locator("#party")).to_contain_text("5 / 6 HP")
        prepared("heal")
        expect(page.locator("#party")).to_contain_text("6 / 6 HP")
        prepared("disable")
        prepared("take_battery")
        expect(page.locator("#quick-actions [data-scene=hangar]")).to_be_visible()
        page.locator("#quick-actions [data-scene=dome]").click()
        expect(page.locator("#scene-number")).to_contain_text("Купол")
        prepared("install")
        prepared("start")
        expect(page.locator("#primary-action")).to_have_text("Завершить приключение →")
        page.locator("#primary-action").click()
        page.reload()
        expect(page.locator("#step-title")).to_have_text("Маяк снова светит")
        page.screenshot(path=str(output / "custom-story.png"), full_page=True)
        page.locator("[data-tab=rules]").click()
        expect(page.locator("#custom-rules")).to_contain_text("d6")
        expect(page.locator("#builtin-rules")).not_to_be_visible()
        expect(page.locator("#custom-rules")).to_contain_text("Эхо")
        page.locator("[data-tab=library]").click()
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Horizontal overflow"
        page.screenshot(path=str(output / "import-mobile.png"), full_page=True)
        assert not errors, errors
        token = page.locator("meta[name=dnd-token]").get_attribute("content")
        browser.close()
    return token


def main(exercise_flow=exercise, output_dir="build/browser-import"):
    parser = argparse.ArgumentParser()
    parser.add_argument("--url")
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--output", type=Path, default=Path(output_dir))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    process = None
    with tempfile.TemporaryDirectory(prefix="dnd-browser-") as temp:
        try:
            url = args.url
            if not url:
                executable = (
                    [str(args.executable.resolve())]
                    if args.executable
                    else [sys.executable, "-m", "dnd_helper"]
                )
                process = subprocess.Popen(
                    [*executable, "--no-browser", "--port", "0", "--data-dir", str(Path(temp) / "Моя игра")],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
                for _ in range(300):
                    if process.poll() is not None:
                        raise RuntimeError(process.stderr.read().decode(errors="replace"))
                    try:
                        port = json.loads((Path(temp) / "Моя игра/runtime.json").read_text("utf-8"))["port"]
                        candidate = f"http://127.0.0.1:{port}"
                        with urllib.request.urlopen(candidate + "/health", timeout=1):
                            url = candidate
                            break
                    except (OSError, ValueError):
                        time.sleep(0.1)
                assert url, "App did not start"
            token = exercise_flow(url, args.output)
            if process:
                req = urllib.request.Request(
                    url + "/api/shutdown",
                    data=b"{}",
                    headers={"Content-Type": "application/json", "X-Dnd-Token": token},
                )
                with urllib.request.urlopen(req, timeout=5):
                    pass
                process.wait(timeout=35)
                assert process.returncode == 0
        finally:
            if process:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                process.stderr.close()
    print(f"Browser acceptance passed: {exercise_flow.__module__}.{exercise_flow.__name__}")


if __name__ == "__main__":
    main()
