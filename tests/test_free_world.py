import threading
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from dnd_helper.engine import uid
from dnd_helper.free_world import (
    FreeWorld,
    Advice,
    DirectorCard,
    RevealEffect,
    initial_world,
    apply_operations,
    available_discoveries,
    build_scene_context,
    compile_director_card,
    complete_routine_steps,
    shortest_path,
    travel_plan,
    validate_proposal,
)
from dnd_helper.rules import GameError
from dnd_helper.web import create_app
from dnd_helper.world_documents import example_document, export_journal, restore_journal, start_document


def proposal(ops=None, check=None):
    return Advice.model_validate(
        dict(
            summary="Решение",
            gm_hint="Спросите игрока о намерении; ведущий решает сам.",
            intent={"kind": "other", "goal": "Разрешить заявку", "targets": []},
            stop="check" if check else "completed",
            evidence=["water"],
            question=None,
            speaker=None,
            check=check,
            success={"summary": "Получилось", "read_aloud": "Мира осмотрелась.", "operations": ops or []},
            failure={"summary": "Не получилось", "read_aloud": "Попытка не удалась.", "operations": []}
            if check
            else None,
        )
    )


class FakeProvider:
    def __init__(self, plan=None):
        self.plan = plan or proposal()
        self.inputs = []
        self.fail_narrate = False
        self.fail_assist = False

    def structured(self, instruction, payload, schema, cancel, model=""):
        self.inputs.append((schema, deepcopy(payload)))
        if self.fail_assist:
            raise GameError("Временный отказ")
        return self.plan, {
            "usage": {"input_tokens": 10, "output_tokens": 10},
        }

    def close(self):
        pass


def command(world, kind, **data):
    state = world.store.read()
    world.command(kind, data, uid(), state["revision"] if state else None)
    if world.thread:
        world.thread.join(timeout=4)
        assert not world.thread.is_alive()
    return world.store.read()


def test_world_does_not_apply_until_accepted_and_undo_keeps_usage(tmp_path):
    ops = [
        {"op": "transfer", "item": "bucket", "before": "square", "destination": "mira"},
        {
            "op": "remember",
            "id": "promise_ada",
            "text": "Мира обещала Аде найти воду.",
            "status": "promise",
            "visibility": "public",
            "known_by": ["mira", "ada"],
        },
    ]
    provider = FakeProvider(proposal(ops))
    w = FreeWorld(tmp_path, provider)
    command(w, "new")
    s = command(w, "submit", text="Возьму ведро и обещаю найти воду.")
    assert s["pending"]["phase"] == "ready"
    assert s["entities"]["bucket"]["location"] == "square"
    assert "promise_ada" not in s["facts"]
    pid = uid()
    s = w.command("accept", {}, pid, s["revision"])["game"]
    assert s["entities"]["bucket"]["location"] == "mira"
    assert s["facts"]["promise_ada"]["status"] == "promise"
    assert len(s["events"]) == 1
    w.command("accept", {}, pid, 0)  # Delivery retry with same receipt, no duplicate effects.
    assert len(w.store.read()["events"]) == 1
    restored = FreeWorld(tmp_path, provider)
    assert restored.store.read()["facts"]["promise_ada"]["source"] == s["events"][0]["id"]
    s = command(restored, "undo")
    assert s["entities"]["bucket"]["location"] == "square"
    assert "promise_ada" not in s["facts"]
    assert len(s["calls"]) == 1


def test_atomic_invalid_second_effect_and_double_spend():
    w = initial_world()
    with pytest.raises(GameError):
        apply_operations(
            w,
            [
                {"op": "consume", "item": "bandage", "owner": "mira"},
                {"op": "consume", "item": "bandage", "owner": "mira"},
            ],
            "mira",
            "event",
        )
    assert w["entities"]["bandage"]["location"] == "mira"
    with pytest.raises(GameError):
        apply_operations(
            w,
            [{"op": "transfer", "item": "magic_sword", "before": "square", "destination": "mira"}],
            "mira",
            "event",
        )


def test_new_place_persists_and_can_return(tmp_path):
    ops = [
        {
            "op": "create",
            "entity": {
                "id": "grove",
                "kind": "location",
                "name": "Роща",
                "description": "Сосны у ручья.",
                "location": "square",
                "goal": "",
            },
        },
        {"op": "move", "entity": "mira", "before": "square", "destination": "grove"},
    ]
    w = FreeWorld(tmp_path, FakeProvider(proposal(ops)))
    command(w, "new")
    command(w, "submit", text="Ищу тропу в рощу.")
    s = command(w, "accept")
    assert s["entities"]["mira"]["location"] == "grove"
    s = apply_operations(
        s, [{"op": "move", "entity": "mira", "before": "grove", "destination": "square"}], "mira", "e2"
    )
    assert "grove" in s["entities"]


def test_roll_uses_prepared_branch_without_second_call(tmp_path):
    provider = FakeProvider(proposal(check={"stat": "agility", "difficulty": "hard"}))
    w = FreeWorld(tmp_path, provider)
    command(w, "new")
    s = command(w, "submit", text="Спускаюсь в колодец.")
    assert s["pending"]["phase"] == "check"
    s = command(w, "roll", value=4)
    assert s["pending"]["phase"] == "ready"
    roll = s["pending"]["roll"]
    assert roll == dict(value=4, bonus=2, dc=16, success=False)
    with pytest.raises(GameError):
        command(w, "roll", value=20)
    assert len(provider.inputs) == 1


def test_path_and_available_discovery_are_deterministic():
    w = initial_world()
    w["connections"].append(["forest", "spring"])
    w["entities"]["spring"] = dict(
        id="spring", kind="location", name="Ручей", description="", location=None, goal=""
    )
    w["facts"]["source"] = dict(
        id="source", text="Вода идёт из ручья.", status="fact", visibility="gm", known_by=[]
    )
    w["document"] = {"discoveries": [{"id": "source_clue", "fact": "source", "at": "spring"}]}
    assert shortest_path(w, "square", "spring") == ["square", "forest", "spring"]
    w["entities"]["mira"]["location"] = "spring"
    assert available_discoveries(w, "mira") == [
        {"id": "source_clue", "fact": "source", "at": "spring"}
    ]


def test_director_presents_clue_before_revealing_it():
    world = start_document(example_document())
    world["entities"]["mira"]["location"] = "water_intake"
    action = {
        "id": "semantic_turn",
        "actor": "mira",
        "participants": ["mira"],
        "text": "Осматриваюсь",
        "mode": "action",
    }
    visible = DirectorCard.model_validate(
        {
            "summary": "Затвор замечен",
            "gm_hint": "",
            "intent": {"kind": "observe", "goal": "Осмотреть место", "targets": ["sluice"]},
            "stop": "completed",
            "progress": "meaningful",
            "evidence": [],
            "question": None,
            "speaker": None,
            "check": None,
            "success": {
                "summary": "Затвор замечен",
                "read_aloud": "У стены виден заклинивший затвор.",
                "effects": [{"effect": "present", "entity": "sluice"}],
            },
            "failure": None,
        }
    )
    proposal = compile_director_card(world, action, visible)
    assert [op.op for op in proposal.success.operations] == ["present"]
    world = apply_operations(world, proposal.success.operations, "mira", action["id"], ["mira"])
    assert "water_truth" not in world["facts"] or "mira" not in world["facts"]["water_truth"]["known_by"]

    revealed = visible.model_copy(deep=True)
    revealed.success.effects = [RevealEffect(effect="reveal", fact="water_truth", knowers=["mira"])]
    proposal = compile_director_card(world, action, revealed)
    assert [op.op for op in proposal.success.operations] == ["discover"]


def test_director_derives_exact_transition_from_transition_id():
    world = start_document(example_document())
    world["entities"]["mira"]["location"] = "water_intake"
    world["entities"]["pry_bar"]["location"] = "mira"
    action = {
        "id": "transition_turn",
        "actor": "mira",
        "participants": ["mira"],
        "text": "Освобождаю затвор ломиком",
        "mode": "action",
    }
    card = DirectorCard.model_validate(
        {
            "summary": "Попытка освободить затвор",
            "gm_hint": "",
            "intent": {"kind": "manipulate", "goal": "Освободить затвор", "targets": ["sluice"]},
            "stop": "check",
            "progress": "meaningful",
            "evidence": [],
            "question": None,
            "speaker": None,
            # The model guessed the wrong check; the authored method remains authoritative.
            "check": {"stat": "agility", "difficulty": "hard", "actor": "mira", "helpers": []},
            "success": {
                "summary": "Затвор освобождён",
                "read_aloud": "Ломик сдвигает заклинивший затвор.",
                "effects": [{"effect": "transition", "transition": "clear_sluice", "using": []}],
            },
            "failure": {"summary": "Не поддался", "read_aloud": "Затвор не сдвинулся.", "effects": []},
        }
    )
    proposal = compile_director_card(world, action, card)
    operation = proposal.success.operations[0]
    assert operation.op == "set_state"
    assert operation.before == "jammed" and operation.after == "clear"
    assert operation.method == "leverage" and operation.using == ["pry_bar"]
    assert proposal.check.stat == "strength" and proposal.check.difficulty == "easy"


def test_check_rejects_identical_nonempty_branches():
    world = start_document(example_document())
    action = {"id": "risk", "actor": "mira", "participants": ["mira"], "text": "Рискую", "mode": "action"}
    same = [{"op": "advance_thread", "thread": "village_water", "before": 0, "after": 1}]
    card = proposal(same, check={"stat": "mind", "difficulty": "standard"})
    card.failure.operations = deepcopy(card.success.operations)

    with pytest.raises(GameError, match="Бросок бессмысленен"):
        validate_proposal(world, action, card)


def test_turn_folder_separates_unintroduced_people_and_precomputes_mechanics():
    world = start_document(example_document())
    world["entities"]["hidden_clerk"] = {
        "id": "hidden_clerk",
        "kind": "npc",
        "name": "Скрытый писец",
        "description": "Сидит за перегородкой.",
        "location": "water_intake",
        "goal": "Не показываться без причины.",
        "capabilities": [],
        "state": "alive",
        "states": ["alive", "dead"],
    }
    world["entities"]["iron_grate"] = {
        "id": "iron_grate",
        "kind": "feature",
        "name": "Железная решётка",
        "description": "Закрывает нишу.",
        "location": "water_intake",
        "goal": "",
        "capabilities": [],
        "state": "closed",
        "states": ["closed", "open"],
    }
    world["entities"]["mira"]["location"] = "water_intake"
    world["entities"]["pry_bar"]["location"] = "mira"
    action = {
        "id": "compact",
        "actor": "mira",
        "participants": ["mira"],
        "text": "Освобождаю затвор ломиком",
        "mode": "action",
    }

    context = build_scene_context(world, action)

    assert "hidden_clerk" not in context["visible_entities"]
    assert "hidden_clerk" not in context["entity_index"]
    assert any(item["id"] == "hidden_clerk" for item in context["possible_introductions"])
    option = next(item for item in context["mechanical_options"] if item["transition"] == "clear_sluice")
    assert option["using"] == ["pry_bar"]
    assert option["check"]["stat"] == "strength" and option["check"]["difficulty"] == "easy"

    action["text"] = "Подхожу к железной решётке"
    inflected = build_scene_context(world, action)
    assert "iron_grate" in inflected["visible_entities"]


def test_transition_preconditions_hide_and_reject_premature_mechanics():
    world = start_document(example_document())
    transition = next(item for item in world["document"]["transitions"] if item["id"] == "clear_sluice")
    transition["when"] = [
        {"source": "entity", "id": "mira", "field": "location", "value": "water_intake", "negate": False}
    ]
    world["entities"]["pry_bar"]["location"] = "mira"
    action = {
        "id": "early",
        "actor": "mira",
        "participants": ["mira"],
        "text": "Чиню затвор ломиком",
        "mode": "action",
    }
    assert not build_scene_context(world, action)["mechanical_options"]
    card = DirectorCard.model_validate(
        {
            "summary": "Рано",
            "gm_hint": "",
            "intent": {"kind": "manipulate", "goal": "Починить", "targets": ["sluice"]},
            "stop": "completed",
            "progress": "meaningful",
            "evidence": [],
            "question": None,
            "speaker": None,
            "check": None,
            "success": {
                "summary": "Готово",
                "read_aloud": "Затвор открыт.",
                "effects": [{"effect": "transition", "transition": "clear_sluice", "using": ["pry_bar"]}],
            },
            "failure": None,
        }
    )
    with pytest.raises(GameError, match="пока недоступен"):
        compile_director_card(world, action, card)


def test_complete_intent_can_move_across_route_discover_and_remember_observation():
    w = start_document(example_document())
    operations = [
        {"op": "move_path", "entity": "mira", "route": ["square", "forest", "water_intake"]},
        {"op": "observe", "subject": "sluice"},
        {"op": "discover", "fact": "water_truth", "knowers": ["mira"]},
    ]
    result = apply_operations(w, operations, "mira", "event")
    assert result["entities"]["mira"]["location"] == "water_intake"
    assert result["facts"]["water_truth"]["known_by"] == ["mira"]
    assert result["observations"]["mira"]["sluice"] == "jammed"
    with pytest.raises(GameError, match="уже осмотрел"):
        apply_operations(result, [{"op": "observe", "subject": "sluice"}], "mira", "again")


def test_observation_can_repeat_after_subject_or_contents_change():
    w = start_document(example_document())
    w = apply_operations(
        w,
        [
            {"op": "move_path", "entity": "mira", "route": ["square", "workshop"]},
            {"op": "observe", "subject": "workshop"},
            {"op": "observe", "subject": "pry_bar"},
        ],
        "mira",
        "look",
    )
    w = apply_operations(
        w,
        [{"op": "transfer", "item": "pry_bar", "before": "workshop", "destination": "mira"}],
        "mira",
        "take",
    )
    apply_operations(w, [{"op": "observe", "subject": "pry_bar"}], "mira", "look_again")
    w = apply_operations(
        w,
        [
            {
                "op": "create",
                "entity": {
                    "id": "chalk",
                    "kind": "item",
                    "name": "Мел",
                    "description": "Кусок мела.",
                    "location": "workshop",
                    "goal": "",
                    "capabilities": [],
                    "state": "",
                    "states": [],
                },
            }
        ],
        "mira",
        "create",
    )
    apply_operations(w, [{"op": "observe", "subject": "workshop"}], "mira", "changed_room")


def test_declared_capability_changes_state_and_share_fact_persists():
    w = start_document(example_document())
    w = apply_operations(
        w,
        [
            {"op": "move_path", "entity": "mira", "route": ["square", "forest", "water_intake"]},
            {"op": "discover", "fact": "water_truth", "knowers": ["mira"]},
        ],
        "mira",
        "discover",
    )
    w = apply_operations(
        w,
        [{"op": "move_path", "entity": "torvin", "route": ["square", "forest", "water_intake"]}],
        "torvin",
        "arrive",
    )
    w = apply_operations(
        w,
        [
            {"op": "share_fact", "fact": "water_truth", "source": "mira", "recipients": ["torvin"]},
            {
                "op": "set_state",
                "entity": "sluice",
                "before": "jammed",
                "after": "clear",
                "method": "repair",
                "using": ["torvin"],
            },
        ],
        "torvin",
        "repair",
    )
    assert "torvin" in w["facts"]["water_truth"]["known_by"]
    assert w["entities"]["sluice"]["state"] == "clear"


def test_selected_nearby_tool_is_carried_through_every_roll_branch():
    w = start_document(example_document())
    w["entities"]["torvin"]["location"] = "workshop"
    plan = Advice.model_validate(
        {
            "summary": "Торвин берёт рычаг и освобождает затвор.",
            "gm_hint": "Попросите бросок силы.",
            "intent": {"kind": "manipulate", "goal": "Освободить затвор", "targets": ["sluice", "pry_bar"]},
            "stop": "check",
            "evidence": ["water_truth"],
            "question": None,
            "speaker": None,
            "check": {"stat": "strength", "difficulty": "easy"},
            "success": {
                "summary": "Затвор свободен.",
                "read_aloud": "Затвор открывается.",
                "operations": [
                    {"op": "move_path", "entity": "torvin", "route": ["workshop", "square", "forest", "water_intake"]},
                    {"op": "set_state", "entity": "sluice", "before": "jammed", "after": "clear", "method": "leverage", "using": ["pry_bar"]},
                ],
            },
            "failure": {
                "summary": "Затвор не поддался.",
                "read_aloud": "Затвор остаётся на месте.",
                "operations": [
                    {"op": "move_path", "entity": "torvin", "route": ["workshop", "square", "forest", "water_intake"]}
                ],
            },
        }
    )
    completed = complete_routine_steps(w, "torvin", plan)
    for outcome in (completed.success, completed.failure):
        assert outcome.operations[0].model_dump() == {
            "op": "transfer",
            "item": "pry_bar",
            "before": "workshop",
            "destination": "torvin",
        }
        apply_operations(w, outcome.operations, "torvin", "event")


def test_routine_normalizer_fixes_discovery_noop_share_and_pickup_order():
    world = start_document(example_document())
    discover = proposal(
        [
            {"op": "move_path", "entity": "mira", "route": ["square", "forest", "water_intake"]},
            {"op": "discover", "fact": "water_truth", "knowers": ["mira"]},
        ]
    ).model_dump()
    discover["intent"] = {"kind": "observe", "goal": "Найти причину", "targets": ["water_intake"]}
    normalized = complete_routine_steps(world, "mira", Advice.model_validate(discover))
    assert [op.op for op in normalized.success.operations] == ["move_path", "observe", "discover"]
    validate_proposal(
        world,
        {"id": "look", "actor": "mira", "participants": ["mira"], "text": "Осматриваю", "mode": "action"},
        normalized,
    )

    redundant = proposal(
        [{"op": "share_fact", "fact": "water", "source": "mira", "recipients": ["torvin"]}]
    )
    assert not complete_routine_steps(world, "mira", redundant).success.operations

    world["entities"]["torvin"]["location"] = "workshop"
    pickup = proposal(
        [
            {"op": "move_path", "entity": "torvin", "route": ["workshop", "square", "forest", "water_intake"]},
            {"op": "transfer", "item": "pry_bar", "before": "workshop", "destination": "torvin"},
        ]
    )
    operations = complete_routine_steps(world, "torvin", pickup).success.operations
    assert [op.op for op in operations] == ["transfer", "move_path"]
    apply_operations(world, operations, "torvin", "pickup")

    pressure = proposal(
        [{"op": "advance_thread", "thread": "village_water", "before": 0, "after": 1}]
    )
    normalized_pressure = complete_routine_steps(world, "mira", pressure)
    assert normalized_pressure.pressure.level == "sign"
    assert normalized_pressure.pressure.thread == "village_water"

    ending = proposal(
        [{"op": "finish_session", "ending": "victory", "summary": "Вода вернулась в деревню."}]
    ).model_dump()
    ending["stop"] = "finished"
    ending["pressure"]["thread"] = "village_water"
    normalized_ending = complete_routine_steps(world, "mira", Advice.model_validate(ending))
    assert normalized_ending.pressure.level == "verdict"
    assert [op.op for op in normalized_ending.success.operations] == ["resolve_thread", "finish_session"]
    validate_proposal(
        world,
        {"id": "end", "actor": "mira", "participants": ["mira"], "text": "Подводим итог", "mode": "action"},
        normalized_ending,
    )


def test_cancel_rejects_late_response_and_no_autoretry(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    class Slow(FakeProvider):
        def structured(self, *args, **kw):
            entered.set()
            release.wait(4)
            return super().structured(*args, **kw)

    w = FreeWorld(tmp_path, Slow())
    command(w, "new")
    s = w.store.read()
    w.command("submit", {"text": "Осмотрюсь"}, uid(), s["revision"])
    assert entered.wait(2)
    s = w.store.read()
    w.command("cancel", {}, uid(), s["revision"])
    release.set()
    w.thread.join(4)
    s = w.store.read()
    assert s["pending"] is None and not s["events"]
    assert len(s["calls"]) == 1


def test_restart_retains_failed_step_without_calling_provider(tmp_path):
    w = FreeWorld(tmp_path, FakeProvider())
    command(w, "new")

    def interrupt(s, db):
        s["pending"] = {"id": "request", "phase": "planning"}
        return s

    w.store.mutate(uid(), None, interrupt)
    new = FreeWorld(tmp_path, FakeProvider())
    assert new.store.read()["pending"]["phase"] == "error"
    assert not new.provider.inputs


def test_world_routes_protected_and_old_game_untouched(tmp_path):
    app = create_app(tmp_path, background=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        h = {"X-Dnd-Token": app.state.csrf}
        assert client.get("/world").status_code == 200
        assert client.get("/api/world").status_code == 403
        assert client.post("/api/codex/login", json={}).status_code == 403
        app.state.service.engine.command("new", {"demo": True})
        old = app.state.service.store.read()
        r = client.post("/api/world/command", json={"kind": "new", "command_id": uid()}, headers=h)
        assert r.status_code == 200
        assert app.state.service.store.read() == old
        assert (
            client.post(
                "/api/world/command", json={"kind": "new", "command_id": uid()}, headers=h
            ).status_code
            == 409
        )


def test_authored_reaction_and_ending_complete_without_model_cooperation(tmp_path):
    document = deepcopy(example_document())
    document["reactions"] = [
        {
            "id": "forest_changes_sluice",
            "when": [
                {"source": "entity", "id": "mira", "field": "location", "value": "forest"}
            ],
            "effects": [{"effect": "set_state", "entity": "sluice", "state": "clear"}],
            "priority": 10,
            "once": True,
            "summary": "Переход вызвал обязательное изменение мира.",
            "read_aloud": "За спиной с сухим стуком освобождается затвор.",
            "halt": True,
        }
    ]
    document["endings"] = [
        {
            "id": "water_restored",
            "when": [
                {"source": "entity", "id": "sluice", "field": "state", "value": "clear"}
            ],
            "priority": 10,
            "ending": "victory",
            "pressure_thread": "village_water",
            "resolve_facts": [],
            "resolve_threads": ["village_water"],
            "summary": "Вода вернулась, и приключение завершено.",
            "read_aloud": "Вода снова идёт к деревне. На этом история заканчивается.",
        }
    ]
    move = {"op": "move", "entity": "mira", "before": "square", "destination": "forest"}
    provider = FakeProvider(proposal([move]))
    world = FreeWorld(tmp_path, provider)
    initial = start_document(document)
    world.store.mutate(uid(), None, lambda _state, _db: initial)

    state = command(world, "submit", text="Ухожу в лес.")
    assert state["pending"]["phase"] == "ready"
    assert [op["op"] for op in state["pending"]["outcome"]["operations"]] == [
        "move",
        "react_state",
        "resolve_thread",
        "finish_session",
    ]
    assert state["pending"]["reactions"] == ["forest_changes_sluice"]
    assert state["pending"]["ending"] == "water_restored"
    assert state["pending"]["proposal"]["pressure"]["thread"] == "village_water"
    assert len(provider.inputs) == 1

    with pytest.raises(GameError, match="нельзя разбирать"):
        command(
            world,
            "edit",
            text=state["pending"]["text"],
            summary=state["pending"]["outcome"]["summary"],
            keep=[1, 2, 3],
        )
    state = command(
        world,
        "edit",
        text="Вода вернулась. История закончена.",
        summary=state["pending"]["outcome"]["summary"],
        keep=[0, 1, 2, 3],
    )
    assert state["pending"]["protected_operations"] == [1, 2, 3]

    state = command(world, "accept")
    assert state["entities"]["sluice"]["state"] == "clear"
    assert state["session"]["status"] == "finished"
    assert state["session"]["ending"] == "victory"
    assert state["fired_reactions"] == ["forest_changes_sluice"]
    assert state["events"][0]["ending"] == "water_restored"
    journal = export_journal(state)
    restored = restore_journal(journal)
    assert restored["session"] == state["session"]
    assert restored["fired_reactions"] == state["fired_reactions"]
    tampered = deepcopy(journal)
    tampered["events"][0]["operations"] = tampered["events"][0]["operations"][:1]
    tampered["events"][0]["protected_operations"] = []
    tampered["events"][0]["reactions"] = []
    tampered["events"][0]["ending"] = None
    with pytest.raises(GameError, match="не совпадают со сценарием"):
        restore_journal(tampered)


def test_guarded_routes_change_with_world_state():
    document = deepcopy(example_document())
    document["routes"] = [
        {
            "between": ["square", "forest"],
            "when": [
                {"source": "entity", "id": "sluice", "field": "state", "value": "clear"}
            ],
            "blocked_text": "Путь пока закрыт.",
        }
    ]
    state = start_document(document)
    assert shortest_path(state, "square", "forest", "mira") is None
    state["entities"]["sluice"]["state"] = "clear"
    assert shortest_path(state, "square", "forest", "mira") == ["square", "forest"]


def test_guarded_distant_goal_uses_safe_prefix_and_stops_at_obstacle():
    world = start_document(example_document())
    world["document"]["routes"] = [
        {
            "between": ["forest", "water_intake"],
            "when": [
                {
                    "source": "entity",
                    "id": "sluice",
                    "field": "state",
                    "value": "clear",
                    "negate": False,
                }
            ],
            "match": "all",
            "blocked_text": "Дальше путь перекрыт потоком.",
        }
    ]
    action = {
        "id": "journey",
        "actor": "mira",
        "participants": ["mira"],
        "text": "Иду к лесному водозабору",
        "mode": "action",
    }
    context = build_scene_context(world, action)
    option = next(item for item in context["travel_options"] if item["destination"] == "water_intake")
    assert option == {
        "destination": "water_intake",
        "name": "Лесной водозабор",
        "safe_route": ["square", "forest"],
        "reaches_goal": False,
        "stops_at": "forest",
        "blocked_text": "Дальше путь перекрыт потоком.",
        "meaningful_stop": True,
    }
    assert travel_plan(world, "square", "water_intake", "mira")["route"] == ["square", "forest"]
    card = DirectorCard.model_validate(
        {
            "summary": "Мира идёт к водозабору.",
            "gm_hint": "Разрешите весь путь.",
            "intent": {"kind": "travel", "goal": "Добраться до водозабора", "targets": ["water_intake"]},
            "stop": "completed",
            "progress": "meaningful",
            "evidence": [],
            "question": None,
            "speaker": None,
            "check": None,
            "success": {
                "summary": "Мира пришла к водозабору.",
                "read_aloud": "Мира оказывается у затвора.",
                "effects": [
                    {"effect": "travel", "entity": "mira", "destination": "water_intake"}
                ],
            },
            "failure": None,
        }
    )
    proposal = compile_director_card(world, action, card, context)
    assert proposal.stop == "blocked"
    assert proposal.check is None
    assert [op.model_dump() for op in proposal.success.operations] == [
        {"op": "move_path", "entity": "mira", "route": ["square", "forest"]}
    ]
    assert "перекрыт потоком" in proposal.success.read_aloud
    validate_proposal(world, action, proposal)


def test_unblocked_distant_goal_still_finishes_in_one_route():
    world = start_document(example_document())
    plan = travel_plan(world, "square", "water_intake", "mira")
    assert plan == {
        "route": ["square", "forest", "water_intake"],
        "reached": True,
        "blocked": None,
    }


def test_error_card_does_not_force_cancel_before_new_action(tmp_path):
    provider = FakeProvider()
    provider.fail_assist = True
    world = FreeWorld(tmp_path, provider)
    command(world, "new")
    state = command(world, "submit", text="Первая попытка")
    assert state["pending"]["phase"] == "error"

    provider.fail_assist = False
    state = command(world, "submit", text="Исправленная заявка")
    assert state["pending"]["phase"] == "ready"
    assert state["pending"]["action"]["text"] == "Исправленная заявка"
    assert len(provider.inputs) == 2


def test_model_cannot_forge_authored_reaction(tmp_path):
    forged = {
        "op": "react_state",
        "reaction": "invented",
        "entity": "sluice",
        "before": "jammed",
        "after": "clear",
    }
    world = FreeWorld(tmp_path, FakeProvider(proposal([forged])))
    command(world, "new")
    state = command(world, "submit", text="Пусть мир изменится сам.")
    assert state["pending"]["phase"] == "error"
    assert "добавляет приложение" in state["pending"]["error"]


def test_known_move_uses_one_assistant_call(tmp_path):
    move = {"op": "move", "entity": "mira", "before": "square", "destination": "forest"}
    provider = FakeProvider(proposal([move]))
    w = FreeWorld(tmp_path, provider)
    command(w, "new")
    s = command(w, "submit", text="Иду к лесу.")
    assert s["pending"]["phase"] == "ready"
    assert [schema for schema, _ in provider.inputs] == [DirectorCard]
    command(w, "accept")
    assert w.store.read()["entities"]["mira"]["location"] == "forest"
    assert "model_thread" not in w.store.read()


def test_director_note_is_authoritative_and_each_card_is_stateless(tmp_path):
    provider = FakeProvider()
    world = FreeWorld(tmp_path, provider)
    command(world, "new")
    state = command(world, "direct", text="Покажи существующую зацепку, не раскрывая её содержание.")
    assert not provider.inputs
    assert state["director_note"].startswith("Покажи")

    state = command(world, "submit", text="Осматриваюсь")
    first_payload = provider.inputs[0][1]
    assert first_payload["director_note"] == state["director_note"]
    assert "session_document" not in first_payload
    command(world, "accept")
    assert world.store.read()["director_note"] == ""

    command(world, "submit", text="Продолжаю разговор")
    assert "session_document" not in provider.inputs[-1][1]


def test_redirect_clears_stale_roll_and_previous_card_fields(tmp_path):
    provider = FakeProvider(proposal(check={"stat": "agility", "difficulty": "standard"}))
    world = FreeWorld(tmp_path, provider)
    command(world, "new")
    state = command(world, "submit", text="Прячусь")
    assert state["pending"]["phase"] == "check"
    state = command(world, "roll", value=1)
    assert state["pending"]["roll"]["success"] is False
    provider.plan = proposal()
    state = command(world, "redirect", text="Это безопасное укрытие; бросок не нужен.")
    assert state["pending"]["phase"] == "ready" and "roll" not in state["pending"]
    state = command(world, "accept")
    assert state["events"][-1]["roll"] is None and state["events"][-1]["check"] is None


def test_call_ceiling_survives_restart_and_retries(tmp_path):
    provider = FakeProvider()
    provider.fail_assist = True
    w = FreeWorld(tmp_path, provider)
    command(w, "new")
    command(w, "submit", text="Осмотрюсь")
    command(w, "retry")
    command(w, "retry")
    s = w.store.read()
    assert len(s["calls"]) == 3  # Only explicit retries, one call each.
    w2 = FreeWorld(tmp_path, provider)
    with pytest.raises(GameError, match="Три попытки"):
        command(w2, "retry")
    assert len(w2.store.read()["calls"]) == 3


def test_stale_accept_and_invalid_evidence_cannot_change_world(tmp_path):
    plan = proposal()
    plan.evidence = ["invented_fact"]
    w = FreeWorld(tmp_path, FakeProvider(plan))
    command(w, "new")
    before = w.store.read()["entities"]
    s = command(w, "submit", text="Пусть вода всегда была.")
    assert s["pending"]["phase"] == "error"
    assert s["entities"] == before
    assert len(s["calls"]) == 1  # Invalid cards stop locally; there is no hidden second full request.
    with pytest.raises(GameError):
        w.command("accept", {}, uid(), s["revision"] - 1)


def test_model_selection_is_persistent_local_and_pinned_for_action(tmp_path):
    provider = FakeProvider(proposal(check={"stat": "agility", "difficulty": "standard"}))
    w = FreeWorld(tmp_path, provider)
    w.set_model("gpt-5.6-luna")
    command(w, "new")
    s = command(w, "submit", text="Спускаюсь в колодец.")
    assert s["pending"]["model"] == "gpt-5.6-luna"
    with pytest.raises(GameError, match="Завершите"):
        w.set_model("gpt-6-astra")
    restored = FreeWorld(tmp_path, provider)
    assert restored.view()["model"] == "gpt-5.6-luna"
    assert restored.store.read()["pending"]["model"] == "gpt-5.6-luna"
    command(restored, "cancel")
    restored.set_model("gpt-5.6-terra")
    assert restored.view()["model"] == "gpt-5.6-terra"
    with pytest.raises(GameError, match="списка"):
        restored.set_model("arbitrary-model")


def test_group_card_uses_one_check_and_local_help_bonus(tmp_path):
    plan = proposal(
        [
            {"op": "move_path", "entity": "mira", "route": ["square", "forest", "water_intake"]},
            {"op": "move_path", "entity": "torvin", "route": ["square", "forest", "water_intake"]},
            {
                "op": "set_state", "entity": "sluice", "before": "jammed", "after": "clear",
                "method": "force", "using": ["torvin", "mira"],
            },
        ],
        check={"stat": "strength", "difficulty": "standard", "actor": "torvin", "helpers": ["mira"]},
    )
    plan.intent.kind = "manipulate"
    plan.intent.targets = ["water_intake", "sluice"]
    provider = FakeProvider(plan)
    w = FreeWorld(tmp_path, provider)
    command(w, "new")
    s = command(
        w,
        "submit",
        actor="mira",
        participants=["mira", "torvin"],
        text="Торвин поднимает затвор, а Мира удерживает трос.",
    )
    assert s["pending"]["phase"] == "check"
    assert s["pending"]["action"]["participants"] == ["mira", "torvin"]
    s = command(w, "roll", value=10)
    assert s["pending"]["roll"] == {"value": 10, "bonus": 3, "dc": 13, "success": True}
    assert len(provider.inputs) == 1


def test_group_operations_are_limited_to_selected_living_participants():
    world = start_document(example_document())
    result = apply_operations(
        world,
        [
            {"op": "move", "entity": "mira", "before": "square", "destination": "forest"},
            {"op": "move", "entity": "torvin", "before": "square", "destination": "forest"},
            {"op": "damage", "entity": "torvin"},
        ],
        "mira",
        "group_event",
        ["mira", "torvin"],
    )
    assert result["entities"]["mira"]["location"] == "forest"
    assert result["entities"]["torvin"]["location"] == "forest"
    assert result["entities"]["torvin"]["hp"] == 6
    with pytest.raises(GameError, match="не выбран"):
        apply_operations(world, [{"op": "damage", "entity": "torvin"}], "mira", "bad", ["mira"])


def test_death_is_final_and_last_death_requires_visible_session_ending():
    world = initial_world()
    world["entities"]["mira"]["hp"] = 2
    world["threads"]["collapse"] = {
        "id": "collapse", "title": "Обвал", "stakes": "Жизнь героя", "subjects": ["mira"],
        "pressure": 2, "resolved": False, "outcome": ""
    }
    action = {"id": "fatal", "actor": "mira", "participants": ["mira"], "text": "Рискует жизнью", "mode": "action"}
    fatal = proposal([
        {"op": "advance_thread", "thread": "collapse", "before": 2, "after": 3},
        {"op": "damage", "entity": "mira"},
    ]).model_dump()
    fatal["pressure"] = {"level": "verdict", "thread": "collapse", "reason": "Смертельная опасность была ясна."}
    with pytest.raises(GameError, match="последнего героя"):
        validate_proposal(world, action, Advice.model_validate(fatal))

    fatal["stop"] = "finished"
    fatal["success"]["operations"].append(
        {"op": "resolve_thread", "thread": "collapse", "outcome": "Обвал унёс последнего героя."}
    )
    fatal["success"]["operations"].append(
        {"op": "finish_session", "ending": "tragedy", "summary": "Последний герой погиб в обвале."}
    )
    approved = Advice.model_validate(fatal)
    validate_proposal(world, action, approved)
    result = apply_operations(world, approved.success.operations, "mira", "fatal", ["mira"])
    assert result["entities"]["mira"]["status"] == "dead"
    assert result["session"]["status"] == "finished"
    with pytest.raises(GameError, match="живые"):
        apply_operations(result, [], "mira", "after", ["mira"])


def test_early_success_can_finish_without_artificial_pressure_escalation():
    world = initial_world()
    action = {"id": "final", "actor": "mira", "participants": ["mira"], "text": "Вода возвращена", "mode": "action"}
    ending = proposal(
        [
            {"op": "resolve_thread", "thread": "village_water", "outcome": "Вода снова поступает в деревню."},
            {"op": "finish_session", "ending": "victory", "summary": "Деревня вновь получила воду."},
        ]
    ).model_dump()
    ending["stop"] = "finished"
    ending["pressure"] = {
        "level": "verdict", "thread": "village_water", "reason": "История получила окончательную развязку."
    }
    plan = Advice.model_validate(ending)
    validate_proposal(world, action, plan)
    result = apply_operations(world, plan.success.operations, "mira", "final", ["mira"])
    assert result["threads"]["village_water"]["pressure"] == 0
    assert result["threads"]["village_water"]["resolved"]
    assert result["session"]["ending"] == "victory"


def test_scene_context_is_compact_and_brings_secret_only_into_relevant_scene():
    world = start_document(example_document())
    world["facts"]["old_conversation"] = {
        "id": "old_conversation", "text": "Давний разговор у водозабора.", "status": "fact",
        "visibility": "public", "known_by": ["mira"], "resolved": False,
        "subjects": ["water_intake"], "source": "old",
    }
    action = {"id": "look", "actor": "mira", "participants": ["mira"], "text": "Осматриваю площадь", "mode": "action"}
    context = build_scene_context(world, action)
    assert "lore" not in context["scenario"] and "gm_notes" not in context["scenario"]
    assert "water_truth" not in context["known_facts"]
    assert "old_conversation" not in context["known_facts"]
    assert len(context["recent_events"]) <= 2
    assert set(context["entity_index"]) < set(world["entities"])

    action["text"] = "Идём к водозабору и чиним затвор"
    destination = build_scene_context(world, action)
    assert "sluice" not in destination["visible_entities"]
    assert "sluice" in destination["known_entities"]
    assert destination["mechanical_options"] == []  # No capable tool is carried to the destination.

    world["entities"]["mira"]["location"] = "water_intake"
    action["text"] = "Осматриваю затвор"
    context = build_scene_context(world, action)
    assert "water_truth" not in context["known_facts"]
    assert "old_conversation" in context["known_facts"]
    assert context["available_discoveries"][0]["fact"] == "water_truth"


def test_known_remote_entity_is_not_made_physically_visible_by_a_fact():
    world = start_document(example_document())
    action = {
        "id": "remote",
        "actor": "mira",
        "participants": ["mira"],
        "text": "Что мы знаем о водозаборе?",
        "mode": "action",
    }
    context = build_scene_context(world, action)
    assert "water_intake" in context["known_entities"]
    assert "water_intake" not in context["visible_entities"]


def test_scene_folder_names_adjacent_blocked_route_without_making_it_visible():
    world = start_document(example_document())
    world["entities"]["mira"]["location"] = "forest"
    world["document"]["routes"] = [
        {
            "between": ["forest", "water_intake"],
            "when": [
                {"source": "entity", "id": "sluice", "field": "state", "value": "clear", "negate": False}
            ],
            "match": "all",
            "blocked_text": "Путь перекрыт потоком.",
        }
    ]
    action = {
        "id": "route",
        "actor": "mira",
        "participants": ["mira"],
        "text": "Ищу путь дальше",
        "mode": "action",
    }
    context = build_scene_context(world, action)
    assert context["blocked_routes"] == [
        {"destination": "water_intake", "reason": "Путь перекрыт потоком."}
    ]
    assert "water_intake" in context["known_entities"]
    assert "water_intake" not in context["visible_entities"]


def test_authored_scene_opportunity_keeps_empty_observation_moving():
    world = start_document(example_document())
    world["entities"]["mira"]["location"] = "water_intake"
    action = {
        "id": "scout",
        "actor": "mira",
        "participants": ["mira"],
        "text": "Осматриваюсь и ищу полезные детали",
        "mode": "action",
    }
    context = build_scene_context(world, action)
    card = DirectorCard.model_validate(
        {
            "summary": "Осмотр",
            "gm_hint": "Покажите наблюдаемую деталь.",
            "intent": {"kind": "observe", "goal": "Осмотреться", "targets": []},
            "stop": "completed",
            "progress": "no_progress",
            "evidence": [],
            "question": None,
            "speaker": None,
            "check": None,
            "success": {"summary": "Ничего", "read_aloud": "Мира осматривается.", "effects": []},
            "failure": None,
        }
    )
    proposal = compile_director_card(world, action, card, context)
    assert proposal.progress == "meaningful"
    assert proposal.success.operations[-1].op == "present"
    assert proposal.success.operations[-1].subject == "sluice"
    assert "сломанная ветка" in proposal.success.read_aloud


def test_nonterminal_failed_check_surfaces_a_fresh_scene_opportunity():
    world = start_document(example_document())
    action = {
        "id": "failed-risk",
        "actor": "mira",
        "participants": ["mira"],
        "text": "Пробую рискованный способ у колодца",
        "mode": "action",
    }
    context = build_scene_context(world, action)
    card = DirectorCard.model_validate(
        {
            "summary": "Риск",
            "gm_hint": "Провал меняет сцену.",
            "intent": {"kind": "other", "goal": "Попробовать", "targets": []},
            "stop": "check",
            "progress": "meaningful",
            "evidence": [],
            "question": None,
            "speaker": None,
            "check": {"stat": "agility", "difficulty": "standard"},
            "success": {"summary": "Успех", "read_aloud": "Получилось.", "effects": []},
            "failure": {
                "summary": "Цена",
                "read_aloud": "Попытка сорвалась.",
                "effects": [{"effect": "advance_thread", "thread": "village_water"}],
            },
        }
    )
    proposal = compile_director_card(world, action, card, context)
    assert proposal.failure.operations[-1].op == "present"
    assert proposal.failure.operations[-1].subject == "bucket"
    assert "пустое ведро" in proposal.failure.read_aloud


def test_model_endpoint_requires_csrf_and_valid_model(tmp_path):
    app = create_app(tmp_path, background=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        h = {"X-Dnd-Token": app.state.csrf}
        assert client.post("/api/world/model", json={"model": "gpt-5.6-luna"}).status_code == 403
        response = client.post("/api/world/model", json={"model": "gpt-5.6-luna"}, headers=h)
        assert response.status_code == 200 and response.json()["model"] == "gpt-5.6-luna"
        assert client.post("/api/world/model", json={"model": {}}, headers=h).status_code == 422
        assert client.post("/api/world/model", json={"model": "invalid"}, headers=h).status_code == 409
