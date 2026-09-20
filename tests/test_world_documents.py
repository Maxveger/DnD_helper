import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from dnd_helper.engine import uid
from dnd_helper.free_world import FreeWorld, DirectorCard
from dnd_helper.rules import GameError
from dnd_helper.web import create_app
from dnd_helper.world_documents import (
    default_document,
    start_document,
    validation_report,
    load_document,
    export_journal,
    author_kit,
)
from test_free_world import FakeProvider, command, proposal


def custom_document():
    d = default_document()
    d["title"] = "Ночная обсерватория"
    d["lore"] = "Звёздный прибор остановился. Кто-то украл стеклянную линзу."
    d["gm_notes"] = "Не выдавай разгадку; предложи ведущему две возможные зацепки."
    d["boundaries"] = ["Вор — часовщик. Эта тайна не меняется."]
    hero = deepcopy(next(e for e in d["entities"] if e["id"] == "mira"))
    hero.update(id="torvin", name="Торвин", stats=dict(strength=2, agility=0, mind=1))
    d["entities"].append(hero)
    d["facts"].append(
        dict(
            id="culprit",
            text="Линзу украл часовщик.",
            status="fact",
            visibility="gm",
            known_by=[],
            resolved=False,
        )
    )
    return d


def test_custom_scenario_and_second_hero_enter_one_call_with_canon(tmp_path):
    p = FakeProvider(proposal(check=dict(stat="strength", difficulty="standard")))
    w = FreeWorld(tmp_path, p)
    doc = custom_document()
    command(w, "import", text=json.dumps(doc))
    s = command(w, "submit", actor="torvin", text="Игрок хочет поднять завал.")
    assert s["pending"]["phase"] == "check"
    assert len(p.inputs) == 1 and p.inputs[0][0] is DirectorCard
    payload = p.inputs[0][1]
    assert payload["scenario"]["title"] == doc["title"]
    # A remote secret and the full document are absent from this independent turn folder.
    assert "culprit" not in payload["known_facts"]
    assert "session_document" not in payload
    assert "gm_notes" not in payload["scenario"]
    assert payload["action"]["actor"] == "torvin"
    s = command(w, "roll", value=11)
    assert s["pending"]["phase"] == "ready"
    assert s["pending"]["roll"]["success"] is True
    assert s["pending"]["roll"]["bonus"] == 2
    assert len(p.inputs) == 1  # No new request after a physical roll, even after restart.
    reopened = FreeWorld(tmp_path, p)
    s = command(reopened, "accept")
    assert len(s["events"]) == 1
    assert len(p.inputs) == 1
    assert not w.store.outbox()  # A secret-bearing draft is never published by this mode.


def test_advice_is_not_world_memory_and_cannot_change_inventory(tmp_path):
    p = FakeProvider(proposal([dict(op="transfer", item="bucket", before="square", destination="mira")]))
    w = FreeWorld(tmp_path, p)
    command(w, "new")
    s = command(w, "submit", mode="hint", text="Игроки растерялись, что предложить?")
    assert s["pending"]["phase"] == "error"
    assert s["entities"]["bucket"]["location"] == "square"
    command(w, "cancel")
    p.plan = proposal()
    p.plan.stop = "advice"
    command(w, "submit", mode="hint", text="Подскажи темп сцены.")
    command(w, "accept")
    command(w, "submit", text="Игрок осматривает площадь.")
    assert p.inputs[-1][1]["recent_events"] == []


def test_edit_card_removes_effects_atomically_and_survives_reload(tmp_path):
    ops = [dict(op="transfer", item="bucket", before="square", destination="mira")]
    w = FreeWorld(tmp_path, FakeProvider(proposal(ops)))
    command(w, "new")
    command(w, "submit", text="Просит ведро.")
    command(
        w, "edit", text="Ада просит сначала объяснить план.", summary="Ведро остаётся у колодца.", keep=[]
    )
    reopened = FreeWorld(tmp_path, w.provider)
    s = command(reopened, "accept")
    assert s["entities"]["bucket"]["location"] == "square"
    assert s["events"][-1]["edited"]
    assert s["events"][-1]["text"] == "Ада просит сначала объяснить план."
    assert len(s["calls"]) == 1


def test_edit_cannot_leave_invalid_dependent_operations(tmp_path):
    plan = proposal(
        [
            dict(
                op="create",
                entity=dict(
                    id="grove", kind="location", name="Роща", description="Сосны.", location="square", goal=""
                ),
            ),
            dict(op="move", entity="mira", before="square", destination="grove"),
        ]
    )
    w = FreeWorld(tmp_path, FakeProvider(plan))
    command(w, "new")
    s = command(w, "submit", text="Игрок ищет рощу.")
    with pytest.raises(GameError):
        command(w, "edit", text="Переход.", summary="В роще.", keep=[1])
    assert w.store.read()["pending"] == s["pending"]
    assert "grove" not in w.store.read()["entities"]


def test_manual_notes_export_restore_undo_and_memory_after_forty_events(tmp_path):
    p = FakeProvider()
    w = FreeWorld(tmp_path, p)
    command(w, "import", text=json.dumps(custom_document()))
    for n in range(40):
        command(w, "note", actor="torvin", text=f"За столом договорились: заметка {n}.", visibility="gm")
    original = w.store.read()
    journal = export_journal(original)
    assert len(journal["events"]) == 40 and "calls" not in journal
    assert not p.inputs
    report = validation_report(json.dumps(journal))
    assert report["ok"] and report["preview"]["events"] == 40
    s = command(w, "import", text=json.dumps(journal))
    for key in ("entities", "facts", "connections", "events"):
        assert s[key] == original[key]
    s = command(w, "undo")
    assert len(s["events"]) == 39 and len(s["facts"]) == len(original["facts"]) - 1
    command(w, "submit", actor="torvin", text="Что ведущему помнить?")
    assert any(
        item.get("memory", "").endswith("заметка 0.")
        for item in p.inputs[-1][1]["private_constraints"]
    )
    assert len(p.inputs[-1][1]["recent_events"]) == 2
    assert [item["boundary"] for item in p.inputs[-1][1]["private_constraints"] if "boundary" in item] == custom_document()["boundaries"]


def test_journal_replay_rejects_duplicate_events_and_impossible_inventory(tmp_path):
    w = FreeWorld(tmp_path, FakeProvider(proposal([dict(op="consume", item="bandage", owner="mira")])))
    command(w, "new")
    command(w, "submit", text="Тратит перевязь.")
    command(w, "accept")
    j = export_journal(w.store.read())
    event = deepcopy(j["events"][0])
    j["events"].append(event)
    assert not validation_report(json.dumps(j))["ok"]
    j["events"][1]["id"] = j["events"][1]["action"]["id"] = "second"
    report = validation_report(json.dumps(j))
    assert not report["ok"] and "events.1" in report["repair_prompt"]
    before = w.store.read()
    with pytest.raises(GameError):
        command(w, "import", text=json.dumps(j))
    assert w.store.read() == before


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["entities"][0].update(location="missing"),
        lambda d: d["entities"].append(deepcopy(d["entities"][0])),
        lambda d: d["connections"].append(["square", "absent"]),
        lambda d: d["facts"][0].update(known_by=["unknown"]),
        lambda d: d["rules"].update(engine="dnd5e"),
        lambda d: d.update(format="dnd-world@2"),
        lambda d: d.update(execute="rm -rf /"),
    ],
)
def test_author_document_errors_are_actionable_and_local(change):
    d = default_document()
    change(d)
    report = validation_report(json.dumps(d))
    assert not report["ok"] and report["errors"] and report["repair_prompt"]


def test_author_kit_json_fences_duplicate_keys_and_legacy_guidance():
    kit = author_kit()
    assert kit["schema"]["properties"]["format"]["const"] == "dnd-world@1"
    assert kit["journal_schema"]["properties"]["format"]["const"] == "dnd-world-log@1"
    assert validation_report("```json\n" + json.dumps(kit["example"]) + "\n```")["ok"]
    assert not validation_report('{"format":"a","format":"b"}')["ok"]
    assert not validation_report("[")["ok"]
    assert not validation_report("[]")["ok"]
    assert not validation_report("я" * 500001)["ok"]
    assert "прежнего режима" in validation_report('{"format":"dnd-adventure@1"}')["repair_prompt"]


def test_scene_contract_rejects_single_path_and_remote_opportunity():
    document = deepcopy(author_kit()["example"])
    transition = next(item for item in document["transitions"] if item["id"] == "clear_sluice")
    transition["methods"] = transition["methods"][:1]
    report = validation_report(json.dumps(document, ensure_ascii=False))
    assert not report["ok"] and "хотя бы два" in report["repair_prompt"]

    document = deepcopy(author_kit()["example"])
    scene = next(item for item in document["scenes"] if item["id"] == "intake_scene")
    scene["opportunities"][0]["subjects"] = ["bucket"]
    report = validation_report(json.dumps(document, ensure_ascii=False))
    assert not report["ok"] and "физически" in report["repair_prompt"]


def test_world_rejects_decorative_states_that_no_rule_can_change():
    document = default_document()
    bucket = next(entity for entity in document["entities"] if entity["id"] == "bucket")
    bucket.update(state="closed", states=["closed", "read"])

    report = validation_report(json.dumps(document, ensure_ascii=False))

    assert not report["ok"]
    assert "ни transition, ни reaction" in report["repair_prompt"]


def test_export_does_not_include_pending_and_cannot_silently_lose_state(tmp_path):
    w = FreeWorld(tmp_path, FakeProvider())
    command(w, "new")
    command(w, "submit", text="Предложение.")
    j = export_journal(w.store.read())
    assert not j["events"] and "pending" not in j
    s = w.store.read()
    s["entities"]["mira"]["hp"] = 1
    with pytest.raises(GameError, match="не воспроизводит"):
        export_journal(s)


def test_import_export_endpoints_are_protected_and_leave_prepared_game_unchanged(tmp_path):
    app = create_app(tmp_path, background=False)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        h = {"X-Dnd-Token": app.state.csrf}
        for url in ("export", "author-kit"):
            assert c.get("/api/world/" + url).status_code == 403
        assert c.post("/api/world/validate", json={"text": "{}"}).status_code == 403
        kit = c.get("/api/world/author-kit", headers=h).json()
        doc = json.dumps(kit["example"])
        assert c.post("/api/world/validate", json={"text": doc}, headers=h).json()["ok"]
        assert app.state.service.free_world.store.read() is None
        before = app.state.service.store.read()
        assert (
            c.post(
                "/api/world/command",
                json=dict(kind="import", data={"text": doc}, command_id=uid()),
                headers=h,
            ).status_code
            == 200
        )
        j = c.get("/api/world/export", headers=h).json()
        assert load_document(json.dumps(j))["entities"] == start_document(kit["example"])["entities"]
        assert app.state.service.store.read() == before


def test_known_travel_is_local_confirmed_across_complete_route(tmp_path):
    provider = FakeProvider()
    w = FreeWorld(tmp_path, provider)
    command(w, "new")
    s = command(w, "travel", actor="mira", destination="forest")
    assert s["pending"]["phase"] == "ready"
    assert s["entities"]["mira"]["location"] == "square"
    assert not provider.inputs
    s = command(w, "accept")
    assert s["entities"]["mira"]["location"] == "forest"
    s = command(w, "travel", actor="mira", destination="workshop")
    assert s["pending"]["outcome"]["operations"] == [
        {"op": "move_path", "entity": "mira", "route": ["forest", "square", "workshop"]}
    ]
    s = command(w, "accept")
    assert s["entities"]["mira"]["location"] == "workshop"
    assert not provider.inputs
    journal = export_journal(w.store.read())
    assert load_document(json.dumps(journal))["entities"] == s["entities"]


def test_projection_shows_actual_mechanics_even_if_prose_claims_movement(tmp_path):
    p = proposal(
        [
            dict(
                op="create",
                entity=dict(
                    id="grove", kind="location", name="Роща", description="Сосны.", location="square", goal=""
                ),
            )
        ]
    )
    p.success.summary = "Герой уже в роще."  # A semantic mistake is not a movement operation.
    w = FreeWorld(tmp_path, FakeProvider(p))
    command(w, "new")
    command(w, "submit", text="Пойти в рощу.")
    assert w.view()["projection"]["location"] == "Деревенская площадь"
    assert w.store.read()["entities"]["mira"]["location"] == "square"
