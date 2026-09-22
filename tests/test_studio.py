from copy import deepcopy
import threading

import pytest

from dnd_helper.rules import GameError
from dnd_helper.studio import (
    COMPACT_AFTER_INPUT_TOKENS,
    COMPACT_AFTER_TURNS,
    DirectorMove,
    DirectorPulse,
    GameStudio,
    StudioCard,
    StudioChatReply,
    StudioOutcome,
    StudioTurn,
    apply_capsule_delta,
    director_signals,
    initial_state,
    load_dossier,
    uid,
)


class StudioProvider:
    def __init__(self, card=None, chat=None, director=None, session_id=None):
        self.card = card
        self.chat = chat or StudioChatReply(answer="Совет ведущему", observations=["Память не меняется"])
        self.director = director or DirectorPulse(
            status="steady",
            observation="Выбор ясен, темп сохраняется.",
            evidence=[],
            risk="Сейчас вмешательство создаст лишнюю подсказку.",
            urgency="low",
            confidence="high",
            moves=[],
        )
        self.session_id = session_id or "11111111-1111-1111-1111-111111111111"
        self.inputs = []

    def conversation(self, prompt, cancel, model="", session_id=None, schema_class=None):
        self.inputs.append(
            {"prompt": prompt, "session_id": session_id, "schema_class": schema_class}
        )
        if schema_class is DirectorPulse:
            value = self.director
        elif "\nСовет:" in prompt or "\nМета:" in prompt:
            value = StudioTurn(kind="chat", card=None, answer=self.chat.answer, observations=self.chat.observations)
        else:
            value = StudioTurn(kind="card", card=self.card, answer=None, observations=[])
        return value.model_dump_json(), {
            "seconds": 0.1,
            "first_event_seconds": 0.01,
            "first_output_seconds": 0.08,
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "usage_scope": "turn",
            "session_id": session_id or self.session_id,
        }

    def compact(self, session_id, cancel):
        self.inputs.append({"compact": session_id})
        return {
            "seconds": 0.05,
            "first_event_seconds": 0.01,
            "usage": {"input_tokens": 20, "output_tokens": 5},
            "usage_scope": "turn",
            "session_id": session_id,
        }


def capsule(**changes):
    value = deepcopy(load_dossier()["initial_capsule"])
    value.update(changes)
    return value


def delta(scene=None, **changes):
    return {
        "scene": scene,
        "changes": [
            {"section": field, "operation": "add", "value": item}
            for field, entries in changes.items()
            for item in entries
        ],
    }


def resolved_card(next_capsule=None):
    next_capsule = next_capsule or capsule(
        known=load_dossier()["initial_capsule"]["known"] + ["Иво знает общий план Дома"]
    )
    start = load_dossier()["initial_capsule"]
    return StudioCard.model_validate(
        {
            "interpretation": "Иво просит посмотреть карту.",
            "gm_note": "Сайрус показывает только известную схему.",
            "stop": "resolved",
            "canon_used": ["У Сайруса есть грубый план Дома."],
            "outcome": {
                "read_aloud": "Сайрус разворачивает карту к Иво.",
                "summary": "Иво изучил общий план Дома.",
                "capsule_delta": delta(
                    scene=next_capsule["scene"] if next_capsule["scene"] != start["scene"] else None,
                    known=[item for item in next_capsule["known"] if item not in start["known"]],
                ),
                "introduced_details": [],
            },
            "check": None,
        }
    )


def checked_card():
    return StudioCard.model_validate(
        {
            "interpretation": "Иво вскрывает замок.",
            "gm_note": "Одна проверка охватывает попытку.",
            "stop": "check",
            "canon_used": ["У Иво есть отмычки."],
            "outcome": None,
            "check": {
                "stat": "agility",
                "difficulty": "standard",
                "reason": "Замок добротный, но обычный.",
                "success_stake": "Дверь открыта тихо.",
                "failure_stake": "Шум привлекает внимание.",
                "success": {
                    "read_aloud": "Замок тихо поддаётся.",
                    "summary": "Служебная дверь открыта.",
                    "capsule_delta": delta(
                        scene="Иво снаружи у тихо открытой служебной двери.",
                        known=["Служебная дверь открыта"],
                    ),
                    "introduced_details": ["У Дома есть служебная дверь"],
                },
                "failure": {
                    "read_aloud": "Отмычка срывается с громким щелчком.",
                    "summary": "Кто-то внутри мог услышать шум.",
                    "capsule_delta": delta(
                        threats=["Шум у служебной двери мог привлечь внимание"]
                    ),
                    "introduced_details": ["У Дома есть служебная дверь"],
                },
            },
        }
    )


def studio_command(studio, command_kind, **data):
    state = studio.store.read()
    studio.command(command_kind, data, uid(), state["revision"] if state else None)
    if studio.thread:
        studio.thread.join(timeout=4)
        assert not studio.thread.is_alive()
    if studio.director_thread:
        studio.director_thread.join(timeout=4)
        assert not studio.director_thread.is_alive()
    return studio.store.read()


def decision_pulse():
    return DirectorPulse(
        status="decision",
        observation="Две похожие заявки не открыли игрокам нового выбора.",
        evidence=["Иво дважды пытался убедить стражу одной и той же угрозой."],
        risk="Следующая попытка может ощущаться как угадывание формулировки.",
        urgency="medium",
        confidence="high",
        moves=[
            DirectorMove(
                kind="highlight",
                label="Подсветить реакцию стража",
                purpose="Вернуть уже видимую зацепку, не выдавая решения.",
                instruction="В следующей карточке явно покажи, на какой части довода страж отреагировал.",
                tradeoff="Не ослабляй подозрение и не гарантируй проход.",
                changes_canon=False,
            ),
            DirectorMove(
                kind="costly_progress",
                label="Предложить продвижение с ценой",
                purpose="Не оставлять сцену статичной после следующего содержательного подхода.",
                instruction="Допусти продвижение только с явной потерей времени или новой угрозой.",
                tradeoff="Последствие прошлой неудачи остаётся в силе.",
                changes_canon=True,
            ),
        ],
    )


def test_live_card_only_changes_memory_after_gm_accepts(tmp_path):
    provider = StudioProvider(resolved_card())
    studio = GameStudio(tmp_path, provider)
    state = studio_command(studio, "new")
    before = deepcopy(state["capsule"])

    state = studio_command(studio, "submit", mode="action", text="Позволь взглянуть на карту")
    assert state["pending"]["phase"] == "review"
    assert state["capsule"] == before
    assert state["events"] == []
    assert '"id": "eye_studio_slice"' in provider.inputs[0]["prompt"]
    assert '"role": "gm"' in provider.inputs[0]["prompt"]
    assert provider.inputs[0]["session_id"] is None
    assert provider.inputs[0]["schema_class"] is StudioTurn

    state = studio_command(
        studio,
        "accept",
        text="Сайрус разворачивает карту к Иво.",
        apply_capsule=True,
        capsule=state["pending"]["selected_outcome"]["capsule_after"],
    )
    assert "Иво знает общий план Дома" in state["capsule"]["known"]
    assert state["transcript"][-2:] == [
        {"role": "player", "text": "Позволь взглянуть на карту"},
        {"role": "gm", "text": "Сайрус разворачивает карту к Иво."},
    ]
    assert len(state["events"]) == 1

    state = studio_command(studio, "undo")
    assert state["capsule"] == before
    assert state["pending"] is None


def test_roll_is_local_and_selects_model_prepared_branch(tmp_path):
    provider = StudioProvider(checked_card())
    studio = GameStudio(tmp_path, provider)
    studio_command(studio, "new")
    state = studio_command(studio, "submit", mode="action", text="Вскрываю замок отмычками")
    assert state["pending"]["phase"] == "check"
    assert len(provider.inputs) == 1

    state = studio_command(studio, "roll", value=10)
    assert state["pending"]["phase"] == "review"
    assert state["pending"]["roll"] == {"value": 10, "bonus": 3, "dc": 13, "success": True}
    assert state["pending"]["selected_outcome"]["summary"] == "Служебная дверь открыта."
    assert len(provider.inputs) == 1

    before = deepcopy(state["capsule"])
    state = studio_command(
        studio,
        "accept",
        text="Замок тихо поддаётся.",
        apply_capsule=False,
    )
    assert state["capsule"] == before
    assert state["events"][0]["memory_applied"] is False


def test_outcome_rejects_empty_read_aloud():
    with pytest.raises(ValueError):
        StudioOutcome.model_validate(
            {
                "read_aloud": "",
                "summary": "Иво сменил позицию.",
                "capsule_delta": delta(scene="Иво снаружи у таверны."),
                "introduced_details": [],
            }
        )


def test_check_rejects_branch_without_authoritative_change(tmp_path):
    card = checked_card().model_copy(deep=True)
    card.check.success.capsule_delta = type(card.check.success.capsule_delta)()
    studio = GameStudio(tmp_path, StudioProvider(card))
    studio_command(studio, "new")

    state = studio_command(studio, "submit", mode="action", text="Выхожу и скрываюсь в тени")

    assert state["pending"]["phase"] == "error"
    assert "Каждая ветка проверки должна менять положение или память" in state["pending"]["error"]
    assert state["events"] == []
    assert state["capsule"] == capsule()


def test_accept_rejects_empty_spoken_text(tmp_path):
    studio = GameStudio(tmp_path, StudioProvider(resolved_card()))
    studio_command(studio, "new")
    state = studio_command(studio, "submit", mode="action", text="Позволь взглянуть на карту")

    with pytest.raises(GameError, match="от 1 до 3000"):
        studio.command("accept", {"text": "   "}, uid(), state["revision"])

    state = studio.store.read()
    assert state["pending"]["phase"] == "review"
    assert state["events"] == []


def test_advice_and_meta_are_private_and_do_not_change_game(tmp_path):
    provider = StudioProvider(resolved_card())
    studio = GameStudio(tmp_path, provider)
    state = studio_command(studio, "new")
    before = deepcopy(state)

    state = studio_command(studio, "submit", mode="advice", text="Можно ли забрать кошель?")
    assert state["capsule"] == before["capsule"]
    assert state["transcript"] == before["transcript"]
    assert state["events"] == []
    assert state["side_chat"][-1]["mode"] == "advice"
    assert "Совет: Можно ли забрать кошель?" in provider.inputs[-1]["prompt"]
    assert provider.inputs[-1]["session_id"] is None

    state = studio_command(studio, "submit", mode="meta", text="Нужна ли карта игроку?")
    assert state["capsule"] == before["capsule"]
    assert state["transcript"] == before["transcript"]
    assert [item["mode"] for item in state["side_chat"]] == ["advice", "meta"]
    assert provider.inputs[-1]["session_id"] == "11111111-1111-1111-1111-111111111111"
    assert provider.inputs[-1]["schema_class"] is StudioTurn
    assert '"id": "eye_studio_slice"' not in provider.inputs[-1]["prompt"]


def test_accepted_result_is_sent_as_delta_not_full_dossier(tmp_path):
    provider = StudioProvider(resolved_card())
    studio = GameStudio(tmp_path, provider)
    studio_command(studio, "new")
    state = studio_command(studio, "submit", mode="action", text="Покажи карту")
    studio_command(
        studio,
        "accept",
        text="Сайрус разворачивает карту.",
        apply_capsule=True,
        capsule=state["pending"]["selected_outcome"]["capsule_after"],
    )

    state = studio_command(studio, "submit", mode="action", text="Я берусь за заказ")
    assert state["pending"]["phase"] == "review"
    prompt = provider.inputs[-1]["prompt"]
    assert provider.inputs[-1]["session_id"] == "11111111-1111-1111-1111-111111111111"
    assert provider.inputs[-1]["schema_class"] is StudioTurn
    assert "Авторитетные решения ведущего" in prompt
    assert '"type": "accepted"' in prompt
    assert '"id": "eye_studio_slice"' not in prompt


def test_capsule_editor_is_generic_and_dossier_has_no_action_catalog(tmp_path):
    studio = GameStudio(tmp_path, StudioProvider(resolved_card()))
    state = studio_command(studio, "new")
    edited = deepcopy(state["capsule"])
    edited["pending_intents"] = ["Когда Око откроется, Иво хочет решить, смотреть ли на него"]
    state = studio_command(studio, "capsule", capsule=edited)
    assert state["capsule"] == edited

    dossier = load_dossier()
    assert "transitions" not in dossier
    assert "routes" not in dossier
    assert "actions" not in dossier
    assert dossier["watchpoints"]


def test_initial_state_is_a_fresh_slice_not_a_saved_game():
    state = initial_state()
    assert state["events"] == []
    assert state["pending"] is None
    assert state["capsule"]["scene"].startswith("Задняя комната")


def test_studio_reopens_when_private_request_is_null(tmp_path):
    provider = StudioProvider(resolved_card())
    studio = GameStudio(tmp_path, provider)
    state = studio_command(studio, "new")
    assert state["assistant_pending"] is None

    reopened = GameStudio(tmp_path, provider)
    assert reopened.store.read()["assistant_pending"] is None


def test_capsule_delta_is_applied_locally_without_repeating_unchanged_memory():
    before = capsule(threats=["Стража настороже"])
    change = delta(
        scene="Иво вошёл в Дом.",
        known=["Служебная дверь открыта"],
    )
    change["changes"].append(
        {"section": "threats", "operation": "remove", "value": "Стража настороже"}
    )
    after = apply_capsule_delta(before, change)
    assert after.scene == "Иво вошёл в Дом."
    assert "Служебная дверь открыта" in after.known
    assert after.threats == []
    assert after.inventory == before["inventory"]


def test_capsule_delta_rejects_conflicting_operations():
    change = delta(known=["Противоречивый факт"])
    change["changes"].append(
        {"section": "known", "operation": "remove", "value": "Противоречивый факт"}
    )
    with pytest.raises(GameError, match="противоречива"):
        apply_capsule_delta(capsule(), change)


def test_scene_boundary_compacts_only_after_threshold_and_keeps_game_state(tmp_path):
    next_capsule = capsule(scene="Иво вышел на улицу перед Домом.")
    provider = StudioProvider(resolved_card(next_capsule))
    studio = GameStudio(tmp_path, provider)
    studio_command(studio, "new")

    def age_conversation(state, db):
        state["model_session_id"] = "11111111-1111-1111-1111-111111111111"
        state["model_turns"] = COMPACT_AFTER_TURNS - 1
        state["calls"].append(
            {
                "status": "completed",
                "mode": "action",
                "turn_usage": {"input_tokens": COMPACT_AFTER_INPUT_TOKENS},
            }
        )
        return state

    studio.store.mutate(uid(), None, age_conversation)
    state = studio_command(studio, "submit", mode="action", text="Выхожу на улицу")
    expected = deepcopy(state["pending"]["selected_outcome"])

    def enlarge_latest_context(current, db):
        current["calls"][-1]["turn_usage"]["input_tokens"] = COMPACT_AFTER_INPUT_TOKENS
        return current

    studio.store.mutate(uid(), None, enlarge_latest_context)
    state = studio_command(
        studio,
        "accept",
        text=expected["read_aloud"],
        apply_capsule=True,
        capsule=expected["capsule_after"],
    )
    assert state["capsule"]["scene"] == "Иво вышел на улицу перед Домом."
    assert state["compaction"]["phase"] == "completed"
    assert state["model_compacted_turns"] == COMPACT_AFTER_TURNS
    assert provider.inputs[-1] == {"compact": "11111111-1111-1111-1111-111111111111"}
    assert state["calls"][-1]["mode"] == "compact"
    assert state["calls"][-1]["turn_usage"]["input_tokens"] == 20


def test_director_observes_in_a_separate_session_without_changing_game(tmp_path):
    game = StudioProvider(resolved_card(), session_id="11111111-1111-1111-1111-111111111111")
    observer = StudioProvider(
        director=decision_pulse(),
        session_id="22222222-2222-2222-2222-222222222222",
    )
    studio = GameStudio(tmp_path, game, observer_provider=observer)
    before = studio_command(studio, "new")

    state = studio_command(studio, "director_request")

    assert state["director"]["phase"] == "ready"
    assert state["director"]["pulse"]["status"] == "decision"
    assert state["director_session_id"] == "22222222-2222-2222-2222-222222222222"
    assert state["capsule"] == before["capsule"]
    assert state["transcript"] == before["transcript"]
    assert state["events"] == []
    assert game.inputs == []
    assert observer.inputs[0]["schema_class"] is DirectorPulse
    assert "отдельный режиссёрский наблюдатель" in observer.inputs[0]["prompt"]


def test_only_gm_selected_director_move_reaches_next_game_card(tmp_path):
    game = StudioProvider(resolved_card())
    observer = StudioProvider(director=decision_pulse())
    studio = GameStudio(tmp_path, game, observer_provider=observer)
    studio_command(studio, "new")
    studio_command(studio, "director_request")

    state = studio_command(studio, "director_choose", kind="highlight")
    assert state["director"]["phase"] == "applied"
    assert "Подсветить реакцию стража" in state["director_note"]

    state = studio_command(studio, "submit", mode="action", text="Я уточняю условия заказа")
    prompt = game.inputs[-1]["prompt"]
    assert "Разовое указание ведущего" in prompt
    assert "явно покажи, на какой части довода" in prompt
    assert "Две похожие заявки" not in prompt
    assert state["director_note"] == ""
    assert state["director"] == {"phase": "idle"}


def test_unselected_director_assessment_never_leaks_to_game_model(tmp_path):
    game = StudioProvider(resolved_card())
    observer = StudioProvider(director=decision_pulse())
    studio = GameStudio(tmp_path, game, observer_provider=observer)
    studio_command(studio, "new")
    studio_command(studio, "director_request")
    studio_command(studio, "director_dismiss")

    studio_command(studio, "submit", mode="action", text="Покажи карту")
    prompt = game.inputs[-1]["prompt"]
    assert "Две похожие заявки" not in prompt
    assert "угадывание формулировки" not in prompt
    assert "Разовое указание ведущего" not in prompt


def test_gm_can_replace_director_options_with_a_manual_instruction(tmp_path):
    studio = GameStudio(
        tmp_path,
        StudioProvider(resolved_card()),
        observer_provider=StudioProvider(director=decision_pulse()),
    )
    studio_command(studio, "new")
    studio_command(studio, "director_request")

    state = studio_command(
        studio,
        "direct",
        text="Сохрани строгость, но яснее обозначь цену отказа.",
    )
    assert state["director"]["phase"] == "applied"
    assert state["director"]["choice"]["label"] == "Своё указание ведущего"
    assert state["director_note"].startswith("Сохрани строгость")

    state = studio_command(studio, "direct", text="")
    assert state["director"] == {"phase": "idle"}
    assert state["director_note"] == ""


def test_director_signals_are_sparse_and_deterministic():
    state = initial_state()
    state["events"] = [
        {
            "input": "Я снова убеждаю стража пропустить меня",
            "roll": {"success": False},
            "meaningful_progress": False,
        },
        {
            "input": "Я убеждаю стража снова пропустить меня",
            "roll": {"success": False},
            "meaningful_progress": False,
        },
        {
            "input": "Я опять убеждаю стража пропустить меня",
            "roll": None,
            "meaningful_progress": False,
        },
        {
            "input": "Я опять убеждаю стража пропустить меня",
            "roll": None,
            "meaningful_progress": True,
        },
    ]
    signals = director_signals(state)
    assert "последние две заявки семантически похожи" in signals
    assert "плановая оценка после 4 новых принятых ходов" in signals
    assert not any("проверок подряд" in item for item in signals)
    assert not any("не изменили" in item for item in signals)

    state["events"] = state["events"][:3]
    signals = director_signals(state)
    assert "3 принятых ходов подряд не изменили доступный выбор" in signals


def test_fourth_accepted_turn_starts_background_observation(tmp_path):
    game = StudioProvider(resolved_card())
    observer = StudioProvider(director=decision_pulse())
    studio = GameStudio(tmp_path, game, observer_provider=observer)
    studio_command(studio, "new")

    def add_history(state, db):
        state["events"] = [
            {"input": f"Принятый ход {number}", "meaningful_progress": True, "roll": None}
            for number in range(1, 4)
        ]
        return state

    studio.store.mutate(uid(), None, add_history)
    state = studio_command(studio, "submit", mode="action", text="Позволь взглянуть на карту")
    state = studio_command(
        studio,
        "accept",
        text=state["pending"]["selected_outcome"]["read_aloud"],
        apply_capsule=True,
        capsule=state["pending"]["selected_outcome"]["capsule_after"],
    )

    assert state["director"]["phase"] == "ready"
    assert state["director"]["trigger"] == ["плановая оценка после 4 новых принятых ходов"]
    assert len(observer.inputs) == 1
    assert state["calls"][-1]["mode"] == "director"


def test_new_player_action_discards_an_observer_result_in_flight(tmp_path):
    class BlockingObserver(StudioProvider):
        def __init__(self):
            super().__init__(director=decision_pulse())
            self.started = threading.Event()
            self.release = threading.Event()

        def conversation(self, prompt, cancel, model="", session_id=None, schema_class=None):
            self.started.set()
            self.release.wait(timeout=3)
            return super().conversation(prompt, cancel, model, session_id, schema_class)

    game = StudioProvider(resolved_card())
    observer = BlockingObserver()
    studio = GameStudio(tmp_path, game, observer_provider=observer)
    studio_command(studio, "new")
    state = studio.store.read()
    studio.command("director_request", {}, uid(), state["revision"])
    assert observer.started.wait(timeout=1)

    state = studio.store.read()
    studio.command(
        "submit",
        {"mode": "action", "text": "Покажи карту"},
        uid(),
        state["revision"],
    )
    observer.release.set()
    studio.thread.join(timeout=3)
    studio.director_thread.join(timeout=3)
    state = studio.store.read()
    assert state["director"] == {"phase": "idle"}
    assert state["director_session_id"] is None
    assert state["pending"]["phase"] == "review"


def test_director_schema_rejects_moves_when_no_decision_is_needed():
    invalid = decision_pulse().model_copy(update={"status": "watch"})
    with pytest.raises(GameError, match="без решения"):
        GameStudio._parse_director(invalid.model_dump_json())
