"""Cross-adventure behavior, rejection boundaries and pinned saves."""

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from dnd_helper.adventures import author_kit, validate_document
from dnd_helper.providers import OpenAIProvider
from dnd_helper.rules import GameError
from dnd_helper.service import Service
from dnd_helper.web import create_app


@pytest.fixture
def doc():
    return json.loads(Path("dnd_helper/resources/example-adventure.json").read_text("utf-8"))


def install(s, d):
    result = s.adventures.install(json.dumps(d))
    assert result["ok"], result
    return result["key"]


def start(s, key, heroes=None, demo=True):
    state = s.engine.command("new", {"characters": heroes or ["lea"], "demo": demo, "adventure_key": key})
    if demo:
        s.engine.command("start", {})
    return state


def act(s, name, actor="lea", die=None, target=None):
    state = s.submit(actor, name, intent=name, target=target)
    if state["pending"]["phase"] == "check":
        state = s.engine.command("request_roll", {})
        state = s.engine.command("roll", {"request_id": state["pending"]["request_id"], "die": die or 6})
    assert state["pending"]["phase"] == "ready", state["pending"]
    return s.engine.command("accept", {})


@pytest.mark.parametrize("die", [1, 6])
def test_imported_adventure_full_flow_restart_and_undo(tmp_path, doc, die):
    s = Service(tmp_path)
    key = install(s, doc)
    start(s, key)
    act(s, "read_log")
    s.engine.command("scene", {"scene": "relay"})
    s.submit("lea", "align", intent="align")
    pending = s.engine.command("request_roll", {})["pending"]
    assert pending["check"]["dice"] == 6
    # A different process loads the exact imported rules and outstanding request.
    s = Service(tmp_path)
    with pytest.raises(GameError, match="от 1 до 6"):
        s.engine.command("roll", {"request_id": pending["request_id"], "die": 20})
    s.engine.command("roll", {"request_id": pending["request_id"], "die": die})
    state = s.engine.command("accept", {})
    assert state["world"]["variables"]["aligned"]
    assert state["characters"]["lea"]["hp"] == (5 if die == 1 else 6)
    act(s, "disable")
    act(s, "take_battery")
    s.engine.command("scene", {"scene": "dome"})
    act(s, "install")
    state = s.engine.command("undo", {})
    assert "Батарея" in state["characters"]["lea"]["items"]
    assert state["world"]["variables"]["battery_place"] == "carried"
    act(s, "install")
    state = act(s, "start")
    assert s.view()["presentation"]["can_finish"]
    assert state["world"]["ending"] == "restored"
    state = s.engine.command("finish", {})
    assert state["status"] == "finished"
    assert Service(tmp_path).store.read()["definition"] == state["definition"]


def test_library_is_immutable_and_new_versions_do_not_replace_game(tmp_path, doc):
    s = Service(tmp_path)
    key = install(s, doc)
    start(s, key)
    before = s.store.read()
    assert install(s, doc) == key
    doc["title"] = "Изменённая история"
    other = install(s, doc)  # Even a reused author version gets a distinct content key.
    assert key != other
    assert s.store.read() == before
    assert len(s.adventures.list()) == 2
    start(s, other)
    assert s.view()["catalog"]["title"] == doc["title"]
    s.engine.command("new", {"characters": ["mira"]})
    assert s.view()["catalog"]["title"] == "Сердце водокачки"


def test_closed_exits_once_failure_manual_and_target(tmp_path, doc):
    s = Service(tmp_path)
    start(s, install(s, doc), ["lea", "jan"])
    s.engine.command("scene", {"scene": "relay"})
    with pytest.raises(GameError, match="закрыт"):
        s.engine.command("scene", {"scene": "dome"})
    act(s, "align", die=1)
    state = s.submit("lea", "align", intent="align")
    assert state["pending"]["phase"] == "clarify"
    s.engine.command("discard", {})
    act(s, "heal", actor="jan", target="lea")
    assert s.store.read()["characters"]["lea"]["hp"] == 6
    assert "Аптечка" not in s.store.read()["characters"]["jan"]["items"]
    act(s, "fight")
    act(s, "fight")
    assert s.store.read()["world"]["variables"]["drone_hp"] == 0
    s.engine.command("scene", {"scene": "dome"})
    state = act(s, "shortcut")
    assert state["scene"] == "hangar"
    assert all(c["location"] == "hangar" for c in state["characters"].values())
    s.engine.command("manual", {"text": "Группа отдохнула.", "hp": {"lea": 4}})
    s.engine.command("accept", {})
    assert s.store.read()["characters"]["lea"]["hp"] == 4
    act(s, "evacuate")
    assert s.engine.command("finish", {})["status"] == "finished"


@pytest.mark.parametrize(
    "change, expected",
    [
        (lambda d: d.update(format="dnd-adventure@2"), "format"),
        (lambda d: d["rules"].update(engine="dnd-5e"), "rules.engine"),
        (lambda d: d["rules"].update(dice=7), "rules.dice"),
        (lambda d: d.update(start="missing"), "start"),
        (lambda d: d.update(max_players=6), "max_players"),
        (lambda d: d["characters"]["lea"]["stats"].update(magic=99), "stats"),
        (lambda d: d["actions"]["align"]["check"].update(stat="magic"), "check.stat"),
        (lambda d: d["actions"]["align"].update(failure=None), "actions.align"),
        (
            lambda d: d["actions"]["start"]["success"]["effects"].append({"op": "exec", "code": "x"}),
            "effects",
        ),
        (lambda d: d["actions"]["align"]["success"]["effects"][0].update(value=1), "value"),
        (
            lambda d: (
                d["actions"]["align"]["requires"].append(
                    {"kind": "variable", "ref": "aligned", "value": True, "op": "gte"}
                )
                if "requires" in d["actions"]["align"]
                else d["actions"]["align"].update(
                    requires=[{"kind": "variable", "ref": "aligned", "value": True, "op": "gte"}]
                )
            ),
            ".op",
        ),
        (lambda d: d["actions"]["heal"].update(target="none"), ".who"),
        (lambda d: d["scenes"]["hangar"]["exits"][0].update(to="void"), ".to"),
        (lambda d: d["variables"]["drone_hp"].update(initial=True), "increment"),
        (lambda d: d["items"].update(duplicate="Батарея"), "items"),
        (lambda d: d["objectives"][0].update(when=[{"kind": "hp", "ref": "hp", "value": 3}]), "objectives"),
        (
            lambda d: d.update(
                actions={
                    "noop": {
                        "name": "Пусто",
                        "description": "Пусто",
                        "scenes": ["hangar"],
                        "success": {"text": "Ничего"},
                    }
                }
            ),
            "endings",
        ),
    ],
)
def test_rejects_unsupported_mechanics_and_broken_references(tmp_path, doc, change, expected):
    s = Service(tmp_path)
    before = s.store.read()
    change(doc)
    report = s.adventures.install(json.dumps(doc))
    assert not report["ok"]
    assert expected in json.dumps(report["errors"], ensure_ascii=False), report
    assert report["repair_prompt"] and report["preview"] is None
    assert not s.adventures.list() and s.store.read() == before


@pytest.mark.parametrize(
    "text", ['{"format":1,"format":2}', "{bad", 'hello {"a":1}', "[]", '{"x":NaN}', "[" * 2000, "я" * 260000]
)
def test_parser_returns_actionable_errors(text):
    report, document = validate_document(text)
    assert not report["ok"] and document is None and report["repair_prompt"]


def test_fenced_json_and_manual_rules_are_explicit(doc):
    doc["rules"]["manual_rules"] = ["Ведущий решает споры о невесомости вручную."]
    report, accepted = validate_document("\ufeff```json\n" + json.dumps(doc) + "\n```")
    assert report["ok"] and accepted
    assert any("manual_rules" in w["path"] for w in report["warnings"])
    kit = author_kit()
    assert kit["schema"]["properties"]["format"]["const"] == "dnd-adventure@1"
    assert validate_document(json.dumps(kit["example"]))[0]["ok"]
    assert "JSON SCHEMA" in kit["prompt"]


def test_take_absent_item_rolls_back_every_effect(tmp_path, doc):
    # Structural validity cannot establish every condition path; runtime remains atomic.
    doc["actions"]["evacuate"]["success"]["effects"] = [
        {"op": "set", "ref": "aligned", "value": True},
        {"op": "take", "ref": "battery"},
        {"op": "end", "ref": "evacuated"},
    ]
    s = Service(tmp_path)
    start(s, install(s, doc))
    s.submit("lea", "evacuate", intent="evacuate")
    before = s.store.read()
    with pytest.raises(GameError, match="отсутствующий"):
        s.engine.command("accept", {})
    assert s.store.read() == before


def test_dynamic_provider_catalog_and_player_secrets(tmp_path, doc):
    doc["gm_notes"] = "HIDDEN_GM_SECRET"
    doc["scenes"]["hangar"]["secret"] = "HIDDEN_SCENE_SECRET"
    doc["npcs"]["echo"]["secret"] = "HIDDEN_NPC_SECRET"
    s = Service(tmp_path)
    lobby = start(s, install(s, doc), demo=False)
    s.engine.command("join", {"user_id": 10, "actor": "lea", "token": lobby["invite"]})
    s.engine.command("start", {})
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"intent":"read_log","target":null}'}],
                    }
                ],
            },
        )

    s.config.save({"openai_key": "test", "ai_enabled": True})
    provider = OpenAIProvider(s.config, s.store, httpx.MockTransport(respond))
    result = provider.plan(s.store.read(), {"actor": "lea", "text": "Смотрю записи на пульте"})
    assert result.intent == "read_log"
    enum = captured[0]["text"]["format"]["schema"]["properties"]["intent"]["enum"]
    assert set(enum) == {"read_log", "evacuate", "heal", "unknown"}
    assert "HIDDEN_" not in json.dumps(captured)
    assert "HIDDEN_" not in json.dumps(s.engine.player_view(s.store.read(), 10))
    s.bot.card("10", 10)
    assert "Техника" in s.store.outbox()[-1][1]["text"]
    s.engine.command("scene", {"scene": "relay"})
    s.submit("lea", "align", intent="align")
    state = s.engine.command("request_roll", {})
    assert s.store.outbox()[-1][1]["reply_markup"]["inline_keyboard"][0][0]["text"] == "Бросить d6"
    s.bot.callback("10", 10, "physical:" + state["pending"]["request_id"], "phys")
    assert "d6" in s.store.outbox()[-1][1]["text"]
    assert "HIDDEN_" not in json.dumps(s.store.outbox())


def test_import_api_is_local_and_does_not_start_or_replace_game(tmp_path, doc):
    app = create_app(tmp_path, background=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = {"X-Dnd-Token": app.state.csrf}
        assert client.post("/api/adventures/import", json={"text": json.dumps(doc)}).status_code == 403
        kit = client.get("/api/author-kit", headers=headers).json()
        assert "declarative@1" in kit["prompt"]
        invalid = client.post("/api/adventures/import", json={"text": "bad"}, headers=headers).json()
        assert not invalid["ok"]
        text = json.dumps(doc)
        assert client.post("/api/adventures/validate", json={"text": text}, headers=headers).json()["ok"]
        result = client.post("/api/adventures/import", json={"text": text}, headers=headers).json()
        state = client.get("/api/state", headers=headers).json()
        assert state["game"] is None and len(state["adventures"]) == 2
        r = client.post(
            "/api/command",
            json={
                "kind": "new",
                "data": {"adventure_key": result["key"], "characters": ["lea"]},
                "command_id": "test",
            },
            headers=headers,
        )
        assert r.status_code == 200 and r.json()["catalog"]["dice"] == 6
        # Report cannot silently bypass schema checks on import.
        doc["rules"]["engine"] = "anything"
        assert not client.post(
            "/api/adventures/import", json={"text": json.dumps(doc)}, headers=headers
        ).json()["ok"]
        assert client.get("/api/state", headers=headers).json()["game"] == r.json()["game"]
        assert (
            client.post("/api/adventures/import", content=b" " * 3_100_001, headers=headers).status_code
            == 413
        )


def test_long_player_messages_preserve_text_and_markup(tmp_path):
    s = Service(tmp_path)
    text = "🌌" * 5000
    s.bot.send(10, text, {"keyboard": [["Персонаж"]]})
    messages = [item for _, item in s.store.outbox()]
    assert "".join(item["text"] for item in messages) == text
    assert all(len(item["text"].encode("utf-16-le")) // 2 <= 3600 for item in messages)
    assert "reply_markup" not in messages[0]
    assert messages[-1]["reply_markup"]["keyboard"] == [["Персонаж"]]


def test_six_custom_heroes_and_ally_target_validation(tmp_path, doc):
    for key in ("zoe", "kai", "max", "ada"):
        doc["characters"][key] = dict(doc["characters"]["jan"], name=key)
    doc["max_players"] = 6
    doc["actions"]["heal"]["target"] = "ally"
    s = Service(tmp_path)
    start(s, install(s, doc), list(doc["characters"]))
    p = s.submit("lea", "heal", intent="heal", target="lea")["pending"]
    assert p["phase"] == "clarify"
    s.engine.command("discard", {})
    s.engine.command("manual", {"text": "Ян ранен.", "hp": {"jan": 0}})
    s.engine.command("accept", {})
    act(s, "heal", target="jan")
    assert s.store.read()["characters"]["jan"]["hp"] == 3
