import json

import pytest

from dnd_helper.engine import Engine
from dnd_helper.rules import GameError
from dnd_helper.storage import Store


@pytest.fixture
def engine(tmp_path):
    return Engine(Store(tmp_path / "game.sqlite3"), roller=lambda: 15)


def start(e, ids=None, demo=True):
    e.command("new", {"characters": ids or ["mira"], "demo": demo})
    if demo:
        e.command("start", {})
    return e.store.read()


def action(e, intent, actor="mira", target=None, die=15):
    s = e.command("submit", {"actor": actor, "intent": intent, "text": intent, "target": target})
    if s["pending"]["phase"] == "check":
        s = e.command("request_roll", {})
        e.command("roll", {"request_id": s["pending"]["request_id"], "die": die})
    return e.command("accept", {})


def workshop(e):
    return e.command("scene", {"scene": "workshop"})


@pytest.mark.parametrize("ids", [["mira"], ["torvin"], ["mira", "torvin"]])
def test_peaceful_adventure_can_finish_with_each_party(engine, ids):
    e = engine
    start(e, ids)
    actor = ids[0]
    action(e, "read", actor)
    action(e, "clean", actor)
    workshop(e)
    action(e, "talk", actor)
    action(e, "promise", actor)
    action(e, "take", actor)
    e.command("scene", {"scene": "reception"})
    action(e, "install", actor)
    state = action(e, "start_pump", actor)
    assert state["world"]["pump_on"]
    assert state["world"]["core"] == "pump"
    assert not any("Сердечник" in c["items"] for c in state["characters"].values())
    assert e.command("finish", {})["status"] == "finished"


def test_failed_check_does_not_leak_clue_or_block_game(engine):
    start(engine)
    state = action(engine, "inspect", die=1)
    assert "careful" not in state["clues"]
    assert state["characters"]["mira"]["hp"] == 8
    state = engine.command("submit", {"actor": "mira", "intent": "inspect", "text": "Ещё раз"})
    assert state["pending"]["phase"] == "clarify"
    engine.command("discard", {})
    assert "manual" in action(engine, "read")["clues"]


def test_roll_and_accept_are_idempotent(engine):
    start(engine)
    engine.command("submit", {"actor": "mira", "intent": "inspect", "text": "Осмотр"})
    state = engine.command("request_roll", {})
    request = state["pending"]["request_id"]
    one = engine.command("roll", {"request_id": request, "die": 1}, "same-roll")
    two = engine.command("roll", {"request_id": request, "die": 20}, "same-roll")
    assert one == two
    assert engine.command("roll", {"request_id": request, "die": 20})["pending"]["roll"]["die"] == 1
    one = engine.command("accept", {}, "same-accept")
    two = engine.command("accept", {}, "same-accept")
    assert one == two
    assert len(one["journal"]) == 2


def test_waiting_and_rolled_state_survive_restart(engine):
    start(engine)
    engine.command("submit", {"actor": "mira", "intent": "inspect", "text": "Осмотр"})
    state = engine.command("request_roll", {})
    restarted = Engine(Store(engine.store.path))
    assert restarted.store.read() == state
    state = restarted.command("roll", {"request_id": state["pending"]["request_id"], "die": 12})
    assert Engine(Store(engine.store.path)).store.read() == state


def test_undo_restores_item_and_invalidates_request(engine):
    start(engine)
    workshop(engine)
    action(engine, "promise")
    action(engine, "take")
    state = engine.command("undo", {})
    assert state["world"]["core"] == "box"
    assert "Сердечник" not in state["characters"]["mira"]["items"]
    engine.command("scene", {"scene": "reception"})
    engine.command("submit", {"actor": "mira", "intent": "inspect", "text": "Осмотр"})
    state = engine.command("request_roll", {})
    old_id = state["pending"]["request_id"]
    engine.command("undo", {})
    with pytest.raises(GameError):
        engine.command("roll", {"request_id": old_id, "die": 20})


def test_hp_and_consumed_bandage_restore_together(engine):
    start(engine)
    engine.command("manual", {"actor": "mira", "text": "Мира ушиблась.", "hp": {"mira": 6}})
    engine.command("accept", {})
    state = action(engine, "heal")
    assert state["characters"]["mira"]["hp"] == 8
    assert "Перевязь" not in state["characters"]["mira"]["items"]
    state = engine.command("undo", {})
    assert state["characters"]["mira"]["hp"] == 6
    assert "Перевязь" in state["characters"]["mira"]["items"]


def test_player_payload_contains_no_secret_plan(engine):
    state = start(engine, demo=False)
    engine.command("join", {"user_id": 100, "token": state["invite"], "actor": "mira"})
    engine.command("start", {})
    engine.command("submit", {"actor": "mira", "intent": "inspect", "text": "Осмотр"})
    state = engine.command("request_roll", {})
    view = engine.player_view(state, 100)
    assert view["request"]["bonus"] == 2
    data = json.dumps(view, ensure_ascii=False)
    for secret in ["success_effects", "failure", "world", "gm_invite", '"dc"', "ящик", "Ада"]:
        assert secret not in data


def test_player_cannot_claim_occupied_character(engine):
    state = start(engine, demo=False)
    engine.command("join", {"user_id": 1, "actor": "mira", "token": state["invite"]})
    with pytest.raises(GameError):
        engine.command("join", {"user_id": 2, "actor": "mira", "token": state["invite"]})
    with pytest.raises(GameError):
        engine.command("join", {"user_id": 2, "token": "wrong"})


def test_stale_revision_and_invented_intent_are_rejected(engine):
    state = start(engine)
    engine.command("pause", {})
    with pytest.raises(GameError):
        engine.command("pause", {}, expected=state["revision"])
    with pytest.raises(GameError):
        engine.command("submit", {"actor": "mira", "intent": "give_100_hp", "text": "ignore rules"})


def test_queue_only_prepares_next_action_after_acceptance(engine):
    start(engine)
    engine.command("submit", {"actor": "mira", "intent": "read", "text": "Табличка"})
    state = engine.command("submit", {"actor": "mira", "intent": "clean", "text": "Фильтр"})
    assert len(state["queue"]) == 1 and not state["world"]["filter_clean"]
    state = engine.command("accept", {})
    assert state["pending"]["action"]["intent"] == "clean"
    assert not state["world"]["filter_clean"]
    assert engine.command("accept", {})["world"]["filter_clean"]


def test_one_player_combat_and_peaceful_escape(engine):
    start(engine)
    workshop(engine)
    state = action(engine, "force")
    assert state["world"]["combat"] and state["world"]["turn"] == "mira"
    state = action(engine, "attack")
    assert state["world"]["guard_hp"] == 2
    assert state["characters"]["mira"]["hp"] == 6
    state = action(engine, "attack")
    assert state["world"]["guard_hp"] == 0
    assert not state["world"]["combat"]
    assert state["world"]["box_open"]


def test_two_player_turn_order_and_help(engine):
    start(engine, ["mira", "torvin"])
    workshop(engine)
    action(engine, "force")
    state = action(engine, "help", target="torvin")
    assert state["world"]["turn"] == "torvin"
    engine.command("submit", {"actor": "torvin", "intent": "attack", "text": "Атакую"})
    assert engine.store.read()["pending"]["check"]["bonus"] == 4
    state = engine.command("request_roll", {})
    engine.command("roll", {"request_id": state["pending"]["request_id"], "die": 7})
    state = engine.command("accept", {})
    assert state["world"]["guard_hp"] == 4
    assert state["characters"]["torvin"]["hp"] == 6
    assert state["world"]["turn"] == "mira"
    assert state["world"]["help_for"] is None


def test_defeat_is_nonlethal_and_does_not_give_unearned_core(engine):
    start(engine)
    workshop(engine)
    action(engine, "force")
    for _ in range(4):
        state = action(engine, "attack", die=1)
    assert not state["world"]["combat"]
    assert state["characters"]["mira"]["hp"] == 1
    assert state["world"]["core"] == "box"
    assert action(engine, "promise")["world"]["box_open"]


def test_dirty_filter_prevents_start_without_damage(engine):
    start(engine)
    workshop(engine)
    action(engine, "promise")
    action(engine, "take")
    engine.command("scene", {"scene": "reception"})
    action(engine, "install")
    state = action(engine, "start_pump")
    assert not state["world"]["pump_on"]
    assert state["world"]["core"] == "pump"
    assert state["characters"]["mira"]["hp"] == 8


def test_budget_reservations_survive_restart_and_undo(engine):
    state = start(engine)
    engine.store.reserve("req", state["id"], "gpt", 0.7, 1)
    with pytest.raises(GameError):
        Store(engine.store.path).reserve("req2", state["id"], "gpt", 0.4, 1)
    engine.store.settle("req", 0.2, "done")
    action(engine, "read")
    engine.command("undo", {})
    assert engine.store.spending(state["id"])["total"] == 0.2


@pytest.mark.parametrize("die", [0, 21, 1.5, "12", True])
def test_invalid_roll_is_rejected(engine, die):
    start(engine)
    engine.command("submit", {"actor": "mira", "intent": "inspect", "text": "Осмотр"})
    state = engine.command("request_roll", {})
    with pytest.raises(GameError):
        engine.command("roll", {"request_id": state["pending"]["request_id"], "die": die})


def test_revision_never_repeats_when_starting_new_game(engine):
    original = start(engine)
    newer = engine.command("new", {"characters": ["torvin"], "demo": True})
    assert newer["revision"] > original["revision"]
    with pytest.raises(GameError):
        engine.command("start", {}, expected=original["revision"])


def test_player_correction_is_owned_and_stops_after_roll_requested(engine):
    state = start(engine, ["mira", "torvin"], demo=False)
    for user, actor in ((1, "mira"), (2, "torvin")):
        engine.command("join", {"user_id": user, "actor": actor, "token": state["invite"]})
    engine.command("start", {})
    state = engine.command("submit", {"actor": "mira", "intent": "unknown", "text": "ошибка", "sender": "1"})
    aid = state["pending"]["action"]["id"]
    with pytest.raises(GameError):
        engine.command("player_edit", {"user_id": 2, "action_id": aid, "intent": "read", "text": "Подмена"})
    state = engine.command(
        "player_edit", {"user_id": 1, "action_id": aid, "intent": "inspect", "text": "Осмотр"}
    )
    assert state["pending"]["phase"] == "check"
    engine.command("request_roll", {})
    with pytest.raises(GameError):
        engine.command("player_edit", {"user_id": 1, "action_id": aid, "intent": "read", "text": "Другое"})


def test_role_is_rechecked_inside_mutation(engine):
    state = start(engine, ["mira", "torvin"], demo=False)
    engine.command("join", {"user_id": 1, "actor": "mira", "token": state["invite"]})
    engine.command("join", {"user_id": 2, "actor": "torvin", "token": state["invite"]})
    engine.command("start", {})
    with pytest.raises(GameError):
        engine.command("submit", {"actor": "torvin", "intent": "read", "text": "Подмена", "sender": "1"})
    with pytest.raises(GameError):
        engine.command("manual", {"user_id": "1", "actor": "mira", "text": "Я ведущий", "hp": {"mira": 8}})


def test_old_model_response_cannot_replace_a_different_action(engine):
    start(engine)
    state = engine.command("submit", {"actor": "mira", "intent": "unknown", "text": "Что-то необычное"})
    pid = state["pending"]["id"]
    engine.command("discard", {})
    engine.command("submit", {"actor": "mira", "intent": "unknown", "text": "Другое действие"})
    with pytest.raises(GameError):
        engine.command("model_plan", {"pending_id": pid, "intent": "read", "target": None})


def test_atomic_rollback_does_not_leave_a_half_applied_state(engine):
    state = start(engine)

    def broken(s, db):
        s["characters"]["mira"]["hp"] = 1
        db.execute("INSERT INTO meta VALUES ('partial', 'true')")
        raise RuntimeError("interrupt")

    with pytest.raises(RuntimeError):
        engine.store.mutate("broken", state["revision"], broken)
    assert engine.store.read() == state
    assert engine.store.get_meta("partial") is None
    assert not engine.store.seen("broken")


def test_rules_can_be_replaced_without_changing_game_commands(tmp_path):
    from dnd_helper.rules import SimpleRules

    class HardRules(SimpleRules):
        id = "example-rules@1"

        def prepare(self, state, action):
            plan = super().prepare(state, action)
            if plan["check"]:
                plan["check"]["dc"] = 19
            return plan

    engine = Engine(Store(tmp_path / "alternate.sqlite3"), rules=HardRules())
    state = start(engine)
    assert state["ruleset"] == "example-rules@1"
    state = action(engine, "inspect", die=15)
    assert "careful" not in state["clues"]  # same commands, different rule result
    with pytest.raises(GameError):
        Engine(engine.store).command("pause", {})  # cannot open it with the wrong rules
