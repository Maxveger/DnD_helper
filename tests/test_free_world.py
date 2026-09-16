import threading
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from dnd_helper.engine import uid
from dnd_helper.free_world import (
    FreeWorld,
    Proposal,
    Verdict,
    Story,
    initial_world,
    apply_operations,
    narrative_context,
)
from dnd_helper.rules import GameError
from dnd_helper.web import create_app


def proposal(ops=None, check=None):
    return Proposal.model_validate(
        dict(
            summary="Решение",
            evidence=["water"],
            question=None,
            speaker=None,
            check=check,
            success={"summary": "Получилось", "operations": ops or []},
            failure={"summary": "Не получилось", "operations": []} if check else None,
        )
    )


class FakeProvider:
    def __init__(self, plan=None):
        self.plan = plan or proposal()
        self.inputs = []
        self.fail_narrate = False

    def structured(self, instruction, payload, schema, cancel, model=""):
        self.inputs.append((schema, deepcopy(payload)))
        if schema is Proposal:
            return self.plan, {"usage": {"input_tokens": 10, "output_tokens": 10}}
        if schema is Verdict:
            return Verdict(accepted=True, reason="Согласовано"), {}
        if self.fail_narrate:
            raise GameError("Временный отказ")
        return Story(text="Мира осмотрелась."), {}

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
    assert len(s["calls"]) == 3


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


def test_roll_pinned_on_narrator_failure_and_retry(tmp_path):
    provider = FakeProvider(proposal(check={"stat": "agility", "difficulty": "hard"}))
    provider.fail_narrate = True
    w = FreeWorld(tmp_path, provider)
    command(w, "new")
    s = command(w, "submit", text="Спускаюсь в колодец.")
    assert s["pending"]["phase"] == "check"
    s = command(w, "roll", value=4)
    assert s["pending"]["phase"] == "error"
    roll = s["pending"]["roll"]
    assert roll == dict(value=4, bonus=2, dc=16, success=False)
    with pytest.raises(GameError):
        command(w, "roll", value=20)
    provider.fail_narrate = False
    s = command(w, "retry")
    assert s["pending"]["phase"] == "ready"
    assert s["pending"]["roll"] == roll
    assert sum(schema is Proposal for schema, _ in provider.inputs) == 1


def test_npc_narrator_does_not_receive_private_or_other_knowledge():
    w = initial_world()
    w["facts"]["secret"] = dict(
        id="secret", text="SECRET_MARKER", status="fact", visibility="gm", known_by=["ada"]
    )
    w["facts"]["player_only"] = dict(
        id="player_only", text="PLAYER_MARKER", status="fact", visibility="public", known_by=["mira"]
    )
    w["entities"]["ada"]["goal"] = "PRIVATE_GOAL"
    p = {"action": {"actor": "mira", "text": "Где вода?"}, "proposal": {"speaker": "ada"}}
    context = narrative_context(w, p, {"operations": []})
    assert "SECRET_MARKER" not in str(context)
    assert "PLAYER_MARKER" not in str(context)
    assert "PRIVATE_GOAL" not in str(context)


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


def test_known_move_uses_two_calls_and_critic_still_checks_new_places(tmp_path):
    move = {"op": "move", "entity": "mira", "before": "square", "destination": "forest"}
    provider = FakeProvider(proposal([move]))
    w = FreeWorld(tmp_path, provider)
    command(w, "new")
    s = command(w, "submit", text="Иду к лесу.")
    assert s["pending"]["phase"] == "ready"
    assert [schema for schema, _ in provider.inputs] == [Proposal, Story]
    command(w, "accept")
    assert w.store.read()["entities"]["mira"]["location"] == "forest"


def test_call_ceiling_survives_restart_and_retries(tmp_path):
    provider = FakeProvider()
    provider.fail_narrate = True
    w = FreeWorld(tmp_path, provider)
    command(w, "new")
    command(w, "submit", text="Осмотрюсь")
    command(w, "retry")
    command(w, "retry")
    s = w.store.read()
    assert len(s["calls"]) == 4  # Plan once, then three attempts at narration.
    w2 = FreeWorld(tmp_path, provider)
    with pytest.raises(GameError, match="Три попытки"):
        command(w2, "retry")
    assert len(w2.store.read()["calls"]) == 4


def test_stale_accept_and_failed_critic_cannot_change_world(tmp_path):
    class Reject(FakeProvider):
        def structured(self, instruction, payload, schema, cancel, model=""):
            if schema is Verdict:
                return Verdict(accepted=False, reason="Не согласуется с фактом water"), {}
            return super().structured(instruction, payload, schema, cancel, model)

    op = {
        "op": "remember",
        "id": "false_past",
        "text": "С утра вода была.",
        "status": "fact",
        "visibility": "public",
        "known_by": ["mira"],
    }
    w = FreeWorld(tmp_path, Reject(proposal([op])))
    command(w, "new")
    s = command(w, "submit", text="Пусть вода всегда была.")
    assert s["pending"]["phase"] == "error"
    assert "false_past" not in s["facts"]
    with pytest.raises(GameError):
        w.command("accept", {}, uid(), s["revision"] - 1)
