"""Recovery and complete builtin routes, including failed rolls and retreat."""

from copy import deepcopy

import pytest

from dnd_helper.engine import Engine
from dnd_helper.rules import GameError
from dnd_helper.storage import Store
from test_engine import action, start, workshop


@pytest.fixture
def engine(tmp_path):
    return Engine(Store(tmp_path / "game.sqlite3"), roller=lambda: 15)


@pytest.mark.parametrize("heroes", [["mira"], ["torvin"], ["mira", "torvin"]])
@pytest.mark.parametrize(
    "route",
    [
        "promise",
        "persuade_pass",
        "persuade_fail",
        "pick_pass",
        "pick_fail",
        "victory",
        "defeat",
        "retreat",
        "retreat_then_peace",
    ],
)
def test_complete_routes_after_failed_inspection(engine, heroes, route):
    e = engine
    start(e, heroes)
    actor = heroes[0]
    action(e, "inspect", actor, die=1)
    workshop(e)
    if route == "promise":
        action(e, "promise", actor)
    elif route.startswith("persuade"):
        action(e, "persuade", actor, die=20 if route.endswith("pass") else 1)
        if route.endswith("fail"):
            action(e, "promise", actor)
    elif route.startswith("pick"):
        action(e, "pick", actor, die=20 if route.endswith("pass") else 1)
        if route.endswith("fail"):
            action(e, "promise", actor)
    else:
        s = action(e, "force", actor)
        turns = 0
        while s["world"]["combat"]:
            who = s["world"]["turn"]
            intent = "attack"
            if route.startswith("retreat"):
                intent = "retreat" if turns == 0 or route == "retreat" else "promise"
            s = action(e, intent, who, die=1 if route == "defeat" else 20)
            turns += 1
            assert turns < 20, "Battle did not terminate"
        assert all(c["location"] == s["scene"] for c in s["characters"].values())
        assert all(c["hp"] > 0 for c in s["characters"].values())
        if s["scene"] == "reception":
            workshop(e)
        if not e.store.read()["world"]["box_open"]:
            action(e, "promise", actor)
    # The last hero carries the core: the selected actor matters in a duo.
    carrier = heroes[-1]
    action(e, "take", carrier)
    e.command("scene", {"scene": "reception"})
    availability = e.presentation(e.store.read())["availability"]
    assert not availability[carrier][""]["install"]
    if len(heroes) == 2:
        assert availability[actor][""]["install"]
    action(e, "install", carrier)
    assert not action(e, "start_pump", actor)["world"]["pump_on"]  # Dirty filter is recoverable.
    action(e, "clean", actor)
    action(e, "start_pump", actor)
    assert e.command("finish", {})["status"] == "finished"


def test_saved_invalid_request_and_queue_can_be_cleared_without_world_changes(engine):
    e = engine
    start(e)
    action(e, "inspect", die=1)
    action(e, "read")
    action(e, "clean")
    for _ in range(5):
        e.command("submit", {"actor": "mira", "intent": "start_pump", "text": "Запустить"})
    e = Engine(Store(e.store.path))
    before = e.store.read()
    assert before["pending"]["phase"] == "clarify" and len(before["queue"]) == 4
    view = e.presentation(before)
    assert view["availability"]["mira"][""]["install"]
    assert view["availability"]["mira"][""]["start_pump"]
    assert "мастерскую" in view["guidance"]
    after = e.command("clear_actions", {})
    assert after["pending"] is None and after["queue"] == []
    assert after["world"] == before["world"]
    assert after["characters"] == before["characters"]
    assert after["clues"] == before["clues"]
    assert after["journal"][:-1] == before["journal"]
    workshop(e)
    action(e, "promise")
    action(e, "take")
    e.command("scene", {"scene": "reception"})
    action(e, "install")
    action(e, "start_pump")
    assert e.command("finish", {})["status"] == "finished"


def test_quick_choice_replaces_clarification_preserves_queue_and_rejects_stacking(engine):
    e = engine
    start(e, ["mira", "torvin"])
    e.command("submit", {"actor": "mira", "intent": "unknown", "text": "Импровизация"})
    e.command("submit", {"actor": "mira", "intent": "clean", "text": "Чищу", "source": "telegram"})
    before = e.store.read()
    choice = {"actor": "torvin", "intent": "read", "text": "Читаю"}
    state = e.command("choose", choice)
    assert state["pending"]["action"]["actor"] == "torvin"
    assert state["pending"]["phase"] == "ready"
    assert state["queue"] == before["queue"]
    state = e.store.read()
    for kind, data in [("choose", choice), ("clear_actions", {})]:
        with pytest.raises(GameError, match="завершите"):
            e.command(kind, data)
        assert e.store.read() == state
    s = e.command("accept", {})
    assert s["pending"]["action"]["intent"] == "clean"
    assert s["queue"] == []


def test_availability_is_read_only_and_checks_target_and_turn(engine):
    e = engine
    start(e, ["mira", "torvin"])
    workshop(e)
    s = action(e, "force")
    before = deepcopy(s)
    opts = e.presentation(s)["availability"]
    assert s == before == e.store.read()
    assert opts["mira"][""]["help"]
    assert not opts["mira"]["torvin"]["help"]
    assert opts["torvin"]["mira"]["attack"]
    assert opts["mira"]["torvin"]["heal"]  # Full health.
    action(e, "attack", die=1)
    action(e, "attack", "torvin", die=1)
    opts = e.presentation(e.store.read())["availability"]
    assert not opts["mira"]["mira"]["heal"]


def test_combat_skips_incapacitated_first_hero(engine):
    e = engine
    start(e, ["mira", "torvin"])
    workshop(e)
    e.command("manual", {"actor": "torvin", "text": "Мира выбыла", "hp": {"mira": 0}})
    e.command("accept", {})
    state = action(e, "force", "torvin")
    assert state["world"]["turn"] == "torvin"
    action(e, "retreat", "torvin")
    state = e.store.read()
    assert state["scene"] == "reception"
    assert all(c["location"] == "reception" and c["hp"] > 0 for c in state["characters"].values())


def test_invalid_quick_choice_does_not_create_a_blocking_request(engine):
    e = engine
    start(e)
    before = e.store.read()
    for intent in ("install", "start_pump", "unknown"):
        with pytest.raises(GameError):
            e.command("choose", {"actor": "mira", "intent": intent, "text": intent})
        assert e.store.read() == before


def test_recovery_does_not_cancel_a_waiting_roll(engine):
    e = engine
    start(e)
    e.command("choose", {"actor": "mira", "intent": "inspect", "text": "Осмотр"})
    e.command("request_roll", {})
    before = e.store.read()
    with pytest.raises(GameError):
        e.command("clear_actions", {})
    assert e.store.read() == before
