"""Real UI regression for unavailable actions, stuck queues and retreat to finale.

Runs in an isolated app by default; accepts --executable for packaged acceptance.
"""

from uuid import uuid4

from playwright.sync_api import expect, sync_playwright

from browser_import_check import main


def exercise(url, output):
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1080})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url)
        token = page.locator('meta[name="dnd-token"]').get_attribute("content")

        def api(kind, data=None):
            response = page.request.post(
                url + "/api/command",
                headers={"X-Dnd-Token": token},
                data={"kind": kind, "data": data or {}, "command_id": str(uuid4())},
            )
            assert response.ok, response.text()
            return response.json()["game"]

        def prepared(intent):
            page.locator(f"[data-intent={intent}]").click()
            expect(page.locator("#primary-action")).to_have_text("Продолжить →")
            page.locator("#primary-action").click()
            expect(page.locator("#primary-action")).not_to_have_text("Продолжить →")

        page.locator("#new-game").click()
        page.locator("input[name=character][value=torvin]").check()
        page.locator("#new-form button[type=submit]").click()
        expect(page.locator("#new-dialog")).not_to_be_visible()
        page.locator("#primary-action").click()
        expect(page.locator("[data-intent=install]")).to_have_count(0)
        expect(page.locator("[data-intent=start_pump]")).to_have_count(0)
        expect(page.locator("#quick-actions [data-scene=workshop]")).to_be_visible()
        page.screenshot(path=str(output / "available-actions.png"), full_page=True)
        page.locator("[data-intent=inspect]").click()
        expect(page.locator("#primary-action")).to_contain_text("Попросить бросок")
        expect(page.locator("[data-intent=read]")).to_have_count(0)
        expect(page.locator("#quick-actions [data-scene]")).to_have_count(0)
        page.locator("#primary-action").click()
        page.reload()
        page.locator("#open-roll").click()
        page.locator("#physical-die").fill("1")
        page.locator("#roll-form button[type=submit]").click()
        expect(page.locator("#roll-panel")).to_contain_text("Проверка не пройдена")
        page.locator("#primary-action").click()
        expect(page.locator("[data-intent=inspect]")).to_have_count(0)

        # A freeform unknown is replaced, not appended, by the next quick choice.
        page.locator("#action-text").fill("квакозябра")
        page.locator("#action-form button[type=submit]").click()
        expect(page.locator("#primary-action")).to_have_text("Отменить это действие →")
        page.locator("#actor").select_option("torvin")
        prepared("read")
        expect(page.locator("[data-intent=read]")).to_have_count(0)
        expect(page.locator("#queue-count")).to_have_text("0")
        prepared("clean")
        expect(page.locator("[data-intent=clean]")).to_have_count(0)
        page.reload()
        expect(page.locator("#quick-actions button")).to_have_count(1)
        expect(page.locator("#quick-actions [data-scene=workshop]")).to_be_visible()

        # Reproduce the old persisted state (also reachable from queued Telegram requests).
        for _ in range(5):
            before = api("submit", {"actor": "mira", "intent": "start_pump", "text": "Запустить"})
        page.reload()
        expect(page.locator("#quick-actions [data-scene]")).to_have_count(0)
        expect(page.locator("#clear-actions")).to_have_text("Отменить все заявки (5)")
        page.screenshot(path=str(output / "recovery.png"), full_page=True)
        page.locator("#clear-actions").click()
        expect(page.locator("#queue-count")).to_have_text("0")
        expect(page.locator("#quick-actions [data-scene=workshop]")).to_be_enabled()
        after = page.request.get(url + "/api/state", headers={"X-Dnd-Token": token}).json()["game"]
        assert before["world"] == after["world"]
        assert before["characters"] == after["characters"]

        page.locator("#quick-actions [data-scene=workshop]").click()
        expect(page.locator("#scene-number")).to_contain_text("02")
        prepared("force")
        expect(page.locator("#actor")).to_have_value("mira")
        expect(page.locator("[data-intent=help]")).to_have_count(0)
        expect(page.locator("#quick-actions [data-scene]")).to_have_count(0)
        page.locator("#target").select_option("torvin")
        expect(page.locator("[data-intent=help]")).to_be_enabled()
        page.locator("#target").select_option("")
        prepared("retreat")
        expect(page.locator("#actor")).to_have_value("torvin")
        prepared("promise")
        expect(page.locator("[data-intent=promise]")).to_have_count(0)
        expect(page.locator("[data-intent=talk]")).to_have_count(0)
        prepared("take")
        expect(page.locator("[data-intent=take]")).to_have_count(0)
        page.locator("#quick-actions [data-scene=reception]").click()
        expect(page.locator("#scene-number")).to_contain_text("01")
        page.locator("#actor").select_option("mira")
        expect(page.locator("[data-intent=install]")).to_have_count(0)
        expect(page.locator("#gm-guidance")).to_contain_text("Торвин")
        page.locator("#actor").select_option("torvin")
        prepared("install")
        expect(page.locator("[data-intent=install]")).to_have_count(0)
        prepared("start_pump")
        expect(page.locator("#quick-actions button")).to_have_count(0)
        page.locator("#primary-action").click()
        expect(page.locator("#page-title")).to_have_text("История, которую вы создали")
        page.reload()
        expect(page.locator("#page-title")).to_have_text("История, которую вы создали")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "finale-mobile.png"), full_page=True)
        assert not errors, errors
        browser.close()
        return token


if __name__ == "__main__":
    main(exercise, "build/browser-gameplay")
