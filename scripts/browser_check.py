"""Exercise the real UI against a separately running demo server (no API keys)."""

import argparse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", type=Path, default=Path("/tmp/dnd-browser"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1080})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url)
        page.locator("#new-game").click()
        page.locator("input[name=character][value=torvin]").check()
        page.locator("#new-form button[type=submit]").click()
        expect(page.locator("#new-dialog")).not_to_be_visible()
        page.locator("#primary-action").click()
        expect(page.locator("#primary-action")).to_have_text("Ждём действие игроков")
        page.screenshot(path=str(args.output / "table.png"), full_page=True)

        def prepared(intent):
            page.locator(f"[data-intent={intent}]").click()
            expect(page.locator("#primary-action")).to_have_text("Продолжить →")
            page.locator("#primary-action").click()
            expect(page.locator("#primary-action")).not_to_have_text("Продолжить →")

        page.locator("[data-intent=inspect]").click()
        expect(page.locator("#primary-action")).to_contain_text("Попросить бросок")
        page.locator("#primary-action").click()
        page.locator("#open-roll").click()
        page.locator("#physical-die").fill("11")
        page.locator("#roll-form button[type=submit]").click()
        expect(page.locator("#roll-panel")).to_contain_text("13")
        page.screenshot(path=str(args.output / "check.png"), full_page=True)
        page.locator("#primary-action").click()
        expect(page.locator("#clues")).to_contain_text("Сердечник сняли аккуратно")
        prepared("read")
        prepared("clean")
        page.locator("#quick-actions [data-scene=workshop]").click()
        expect(page.locator("#scene-number")).to_contain_text("02")
        prepared("promise")
        prepared("take")
        page.locator("#quick-actions [data-scene=reception]").click()
        expect(page.locator("#scene-number")).to_contain_text("01")
        prepared("install")
        prepared("start_pump")
        expect(page.locator("#primary-action")).to_have_text("Завершить приключение →")
        page.locator("#primary-action").click()
        expect(page.locator("#page-title")).to_have_text("История, которую вы создали")
        page.reload()
        expect(page.locator("#page-title")).to_have_text("История, которую вы создали")
        page.locator("[data-tab=journal]").click()
        expect(page.locator("#journal-list")).to_contain_text("Приключение завершено")
        page.screenshot(path=str(args.output / "journal.png"), full_page=True)
        page.locator("[data-tab=table]").click()
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(args.output / "mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Horizontal overflow"
        page.locator("#mobile-menu").click()
        page.locator("[data-tab=rules]").click()
        expect(page.locator("#rules-view")).to_be_visible()
        assert not errors, errors
        browser.close()
    print("Browser flow passed: lobby, check, clues, peaceful finale, reload, journal, mobile layout.")


if __name__ == "__main__":
    main()
