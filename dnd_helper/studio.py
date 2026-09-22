"""Experimental live-model game IDE: the model improvises, the human approves memory."""

import json
import os
import re
import secrets
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .codex_provider import CodexProvider
from .rules import GameError, require
from .storage import Store, encode


DEFAULT_MODEL = "gpt-5.6-luna"
WORLD_MODELS = {
    "gpt-5.6-luna": "Luna — экономная",
    "gpt-5.6-terra": "Terra — сбалансированная",
    "gpt-6-astra": "Astra — сложные задачи",
    "": "Из настроек Codex",
}


def uid():
    return secrets.token_hex(8)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Capsule(Strict):
    scene: str = Field(min_length=1, max_length=1200)
    hero: list[str] = Field(default_factory=list, max_length=20)
    inventory: list[str] = Field(default_factory=list, max_length=30)
    known: list[str] = Field(default_factory=list, max_length=40)
    commitments: list[str] = Field(default_factory=list, max_length=30)
    npcs: list[str] = Field(default_factory=list, max_length=30)
    threats: list[str] = Field(default_factory=list, max_length=30)
    pending_intents: list[str] = Field(default_factory=list, max_length=20)


MemoryEntry = Annotated[str, Field(min_length=1, max_length=700)]


class MemoryChange(Strict):
    section: Literal[
        "hero", "inventory", "known", "commitments", "npcs", "threats", "pending_intents"
    ]
    operation: Literal["add", "remove"]
    value: MemoryEntry


class CapsuleDelta(Strict):
    scene: str | None = Field(default=None, min_length=1, max_length=1200)
    changes: list[MemoryChange] = Field(default_factory=list, max_length=30)


class StudioOutcome(Strict):
    read_aloud: str = Field(min_length=1, max_length=2400)
    summary: str = Field(min_length=1, max_length=700)
    capsule_delta: CapsuleDelta
    introduced_details: list[str] = Field(default_factory=list, max_length=12)


class StudioCheck(Strict):
    stat: Literal["strength", "agility", "mind"]
    difficulty: Literal["easy", "standard", "hard"]
    reason: str = Field(min_length=1, max_length=500)
    success_stake: str = Field(min_length=1, max_length=600)
    failure_stake: str = Field(min_length=1, max_length=600)
    success: StudioOutcome
    failure: StudioOutcome


class StudioCard(Strict):
    interpretation: str = Field(min_length=1, max_length=500)
    gm_note: str = Field(min_length=1, max_length=900)
    stop: Literal["resolved", "question", "check"]
    canon_used: list[str] = Field(default_factory=list, max_length=16)
    outcome: StudioOutcome | None = None
    check: StudioCheck | None = None


class StudioChatReply(Strict):
    answer: str = Field(min_length=1, max_length=2200)
    observations: list[str] = Field(default_factory=list, max_length=12)


class StudioTurn(Strict):
    kind: Literal["card", "chat"]
    card: StudioCard | None = None
    answer: str | None = None
    observations: list[str] = Field(default_factory=list, max_length=12)


class DirectorMove(Strict):
    kind: Literal[
        "advance_to_choice", "add_specifics", "costly_opening", "increase_pressure"
    ]
    label: str = Field(min_length=1, max_length=90)
    purpose: str = Field(min_length=1, max_length=500)
    instruction: str = Field(min_length=1, max_length=700)
    tradeoff: str = Field(min_length=1, max_length=500)
    changes_canon: bool


class DirectorPulse(Strict):
    status: Literal["steady", "watch", "decision"]
    observation: str = Field(min_length=1, max_length=800)
    evidence: list[str] = Field(default_factory=list, max_length=8)
    risk: str = Field(min_length=1, max_length=700)
    urgency: Literal["low", "medium", "high"]
    confidence: Literal["low", "medium", "high"]
    moves: list[DirectorMove] = Field(default_factory=list, max_length=4)


LIVE_STUDIO = """Ты — живая модель внутри IDE человека-ведущего настольной игры.
Ты не компилятор сценария и не автономный мастер. Полное режиссёрское досье задаёт истины и характеры,
капсула хранит только уже принятое, а эта беседа обеспечивает живую связность. Человек решает, что принять.

В одной беседе приходят три вида реплик. «Иво:» продолжает сцену. «Совет:» — приватный вопрос ведущего о ситуации.
«Мета:» — приватное обсуждение процесса и движка. Совет и Мета не совершают действие героя, не продолжают сцену
и не меняют капсулу. Для них верни kind=chat, answer и observations, оставив card пустым. Для Иво верни kind=card,
card и оставь answer пустым. Всегда возвращай только один JSON-объект по договорённой схеме, без Markdown.

Разрешай свободное намерение из физической ситуации и целей персонажей. Не ищи заранее правильный маршрут,
не заставляй игрока угадывать авторскую формулировку и не превращай каждую реплику в бросок. NPC знают не всё,
говорят не всё, чего хотят, и отвечают из собственной позиции. Не говори и не выбирай за героя.

Если действие безопасно или разумное предложение само отвечает цели NPC, outcome разрешает его без проверки.
Если есть содержательный риск, check заранее показывает ведущему разные ставки успеха и неудачи и готовит обе ветки.
Одна проверка охватывает связный подход. Неудача создаёт новое положение, а не тупик. Ясные ограничения игрока
(например, «не убивать») обязательны и при неудаче. Натуральная единица не создаёт отдельной катастрофы.

Составное намерение разрешай по порядку: безопасная подготовка может произойти в обеих ветках, зависимое продолжение
происходит только после нужного успеха. Если безопасная физическая часть уже выполнена до рискованной (например,
герой вышел из комнаты перед попыткой скрыться), отрази её одинаковой сменой scene в обеих ветках. У каждой ветки
проверки обязательны непустой read_aloud и хотя бы одно авторитетное изменение: scene либо changes. Не назначай
проверку, если её ветви не создают разных последствий. Условное будущее намерение не исполняй сейчас: запиши его в
pending_intents, а при наступлении условия остановись и верни выбор. Добровольный взгляд в Око не является броском,
если герой уже физически получил возможность: это решение игрока.

Разрешай уже заявленное действие до первого нового содержательного выбора игрока, а не до ближайшего физического
микрошага. Если герой вышел, спрятался и наблюдает, не останавливайся только на укрытии: покажи конкретный доступный
результат наблюдения, если новое препятствие не прервало последовательность. Если текст говорит, что герой успел
заглянуть, обязательно скажи, что именно он увидел. Провал может ограничить сведения и добавить цену, но результат
оплаченного риска должен быть конкретным и менять доступное игроку решение.

capsule_delta — только изменение компактной памяти после исхода, а не полная капсула. scene=null, если ситуация
не меняется; changes содержит только реальные операции add/remove над указанными секциями. Не повторяй неизменные
записи и не записывай атмосферный цвет как постоянную истину. Существенную новую импровизацию перечисли в
introduced_details: она станет каноном только после принятия человеком. read_aloud — только то, что можно прочитать
игроку; секреты и основания оставь в gm_note. Не упоминай технические поля и JSON.

Для stop=resolved или stop=question заполни outcome и оставь check пустым. Для stop=check заполни check и оставь
outcome пустым. В Совете помогай понять ситуацию, NPC, ставки и честные решения, но не выбирай за игроков.
В Мета анализируй качество пайплайна, память, границу модели и программы и замеченные дефекты."""


DIRECTOR_OBSERVER = """Ты — отдельный режиссёрский редактор при человеке-ведущем настольной игры.
Перед тобой уже подготовленная, но ещё не принятая игровая карточка. Ты не играешь мир, не пишешь новую сцену,
не выбираешь действие за игроков и ничего не меняешь самостоятельно. Игровая модель намеренно строгая;
не исправляй честный проигрыш и не пытайся обеспечить победу.

Оцени именно текущий черновик ответа мира. Полностью ли он разрешает заявленное действие, а не останавливается после
подготовительного микрошага? Получил ли игрок конкретный результат оплаченного риска? Заканчивается ли ответ новой
содержательной информацией, угрозой или выбором? Согласованы ли текст, ставки и дельта памяти? При проверке оцени обе
ветки, а после броска не предлагай менять характеристику, сложность или выпавший исход.

Не советуй, что теперь может или должен сделать герой: игрок уже выбрал действие, а следующий выбор принадлежит ему.
Предлагай только способы, которыми ведущий может отредактировать текущий ответ мира:
- advance_to_choice — довести подразумеваемую безопасную последовательность до следующего реального выбора;
- add_specifics — назвать конкретно увиденное, услышанное или изменившееся вместо пустого итога;
- costly_opening — сохранить провал и его цену, но открыть новое трудное положение вместо тупика;
- increase_pressure — сделать уже обещанное последствие ощутимым, если черновик уклоняется от него.

Верни status=steady, если карточка уже годится; status=watch, если недостаток слабый и вмешательство не оправдано;
status=decision, только когда карточку полезно исправить до принятия. Для steady и watch moves оставь пустым.
Для decision предложи 1–3 разных редакторских намерения. Каждый move относится только к текущей карточке и должен
сохранять заявку героя, принятый канон, ограничения игрока и честный результат броска. changes_canon=true, если новая
версия может изменить ещё не принятую дельту, давление, факт или возможность; подтверждает её всё равно ведущий.

Не раскрывай служебную тайну без заработанного основания, не превращай маршрут в подсказку и не перечисляй варианты
действий героя. Если поражение закономерно и карточка ясно его показывает, оставь её строгой. Возвращай только JSON
по схеме, без Markdown."""


COMPACT_AFTER_TURNS = 16
COMPACT_AFTER_INPUT_TOKENS = 40_000
DIRECTOR_PERIODIC_TURNS = 4


def _progress_signature(capsule):
    """Threat-only changes are pressure, not progress toward an informed new choice."""

    value = Capsule.model_validate(capsule)
    return tuple(
        json.dumps(getattr(value, key), ensure_ascii=False, sort_keys=True)
        for key in (
            "scene",
            "hero",
            "inventory",
            "known",
            "commitments",
            "npcs",
            "pending_intents",
        )
    )


def _intent_words(text):
    return {word for word in re.findall(r"[\wёЁ]+", text.lower()) if len(word) >= 4}


def _same_intent(left, right):
    a, b = _intent_words(left), _intent_words(right)
    return bool(a and b and len(a & b) / min(len(a), len(b)) >= 0.6)


def director_signals(state):
    """Cheap gates keep draft review sparse; the model judges meaning only after a gate fires."""

    events = state.get("events", [])
    pending = state.get("pending") or {}
    card = pending.get("card") or {}
    if not card:
        return []
    signals = []
    failure_streak = 0
    for item in reversed(events):
        roll = item.get("roll")
        if roll and roll.get("success") is False:
            failure_streak += 1
        else:
            break
    if failure_streak >= 2:
        signals.append(f"{failure_streak} проверок подряд закончились неудачей")

    no_progress = 0
    for item in reversed(events):
        if item.get("meaningful_progress", True):
            break
        no_progress += 1
    if no_progress >= 3:
        signals.append(f"{no_progress} принятых ходов подряд не изменили доступный выбор")

    if events and _same_intent(pending.get("input", ""), events[-1].get("input", "")):
        signals.append("текущая заявка семантически похожа на предыдущую")

    if card.get("stop") == "check":
        signals.append("текущая карточка содержит проверку и две ветки последствий")

    seen = state.get("director_seen_events", 0)
    if len(events) - seen >= DIRECTOR_PERIODIC_TURNS:
        signals.append(f"плановая редактура после {len(events) - seen} новых принятых ходов")
    return signals


def apply_capsule_delta(capsule, delta):
    """Apply the model's small proposal locally; the model never writes authoritative state."""

    before = Capsule.model_validate(capsule)
    change = CapsuleDelta.model_validate(delta)
    value = before.model_dump()
    if change.scene is not None:
        value["scene"] = change.scene
    operations = [(item.section, item.operation, item.value) for item in change.changes]
    require(len(operations) == len(set(operations)), "Дельта памяти содержит повторы.")
    pairs = {(section, entry) for section, _operation, entry in operations}
    require(
        all(
            not (
                (section, "add", entry) in operations
                and (section, "remove", entry) in operations
            )
            for section, entry in pairs
        ),
        "Дельта памяти противоречива.",
    )
    for item in change.changes:
        current = value[item.section]
        if item.operation == "remove":
            value[item.section] = [entry for entry in current if entry != item.value]
        elif item.value not in current:
            current.append(item.value)
    return Capsule.model_validate(value)


def project_outcome(outcome, capsule):
    value = outcome.model_dump()
    value["capsule_after"] = apply_capsule_delta(capsule, outcome.capsule_delta).model_dump()
    return value


def project_card(card, capsule):
    value = card.model_dump()
    if card.outcome is not None:
        value["outcome"] = project_outcome(card.outcome, capsule)
    if card.check is not None:
        value["check"]["success"] = project_outcome(card.check.success, capsule)
        value["check"]["failure"] = project_outcome(card.check.failure, capsule)
    return value


def load_dossier():
    path = Path(__file__).parent / "resources" / "studio-eye.json"
    return json.loads(path.read_text("utf-8"))


def initial_state(dossier=None):
    dossier = dossier or load_dossier()
    return {
        "id": uid(),
        "dossier_id": dossier["id"],
        "capsule": deepcopy(dossier["initial_capsule"]),
        "transcript": [{"role": "gm", "text": dossier["opening"]}],
        "side_chat": [],
        "events": [],
        "pending": None,
        "assistant_pending": None,
        "model_session_id": None,
        "model_turns": 0,
        "model_compacted_turns": 0,
        "compaction": None,
        "model_updates": [],
        "remind_dossier": False,
        "director_session_id": None,
        "director_turns": 0,
        "director_seen_events": 0,
        "director": {"phase": "idle"},
        "calls": [],
        "created": time.time(),
    }


class GameStudio:
    def __init__(self, directory, provider=None, observer_provider=None):
        directory = Path(directory).resolve()
        self.store = Store(directory / "studio.sqlite3")
        local_cli = directory / "codex-cli/node_modules/.bin" / ("codex.cmd" if os.name == "nt" else "codex")
        executable = str(local_cli) if local_cli.is_file() else None
        self.provider = provider or CodexProvider(executable=executable)
        self.observer_provider = observer_provider or (
            provider if provider is not None else CodexProvider(executable=executable)
        )
        self.owns_provider = provider is None
        self.owns_observer_provider = provider is None and observer_provider is None
        self.thread = None
        self.cancel = threading.Event()
        self.director_thread = None
        self.director_cancel = threading.Event()
        self.lock = threading.RLock()
        state = self.store.read()
        if state and (
            "director_note" in state
            or any(
                key not in state
                for key in (
                    "model_session_id",
                    "model_turns",
                    "model_compacted_turns",
                    "compaction",
                    "model_updates",
                    "remind_dossier",
                    "director_session_id",
                    "director_turns",
                    "director_seen_events",
                    "director",
                )
            )
        ):
            def migrate(current, db):
                current.setdefault("model_session_id", None)
                current.setdefault("model_turns", 0)
                current.setdefault("model_compacted_turns", 0)
                current.setdefault("compaction", None)
                current.setdefault("model_updates", [])
                current.setdefault("remind_dossier", False)
                current.setdefault("director_session_id", None)
                current.setdefault("director_turns", 0)
                current.setdefault("director_seen_events", 0)
                current.setdefault("director", {"phase": "idle"})
                if (
                    current["director"].get("phase") != "idle"
                    and "basis_pending_id" not in current["director"]
                ):
                    current["director"] = {"phase": "idle"}
                current.pop("director_note", None)
                return current

            self.store.mutate(uid(), None, migrate)
            state = self.store.read()
        if state and (state.get("pending", {}).get("phase") == "planning" if state.get("pending") else False):
            self._mutate_pending(
                state["pending"]["id"],
                lambda item: item.update(
                    phase="error", error="Приложение перезапущено. Повторите запрос; память не изменена."
                ),
            )
        state = self.store.read()
        if state and (state.get("assistant_pending") or {}).get("phase") == "planning":
            self._mutate_assistant(
                state["assistant_pending"]["id"],
                lambda item: item.update(
                    phase="error", error="Приложение перезапущено. Повторите приватный вопрос."
                ),
            )
        state = self.store.read()
        if state and (state.get("compaction") or {}).get("phase") == "planning":
            def interrupted_compaction(current, db):
                current["compaction"].update(
                    phase="error",
                    error="Сжатие было прервано перезапуском. Игра и капсула не изменены.",
                )
                return current

            self.store.mutate(uid(), None, interrupted_compaction)
        state = self.store.read()
        if state and (state.get("director") or {}).get("phase") == "planning":
            def interrupted_director(current, db):
                current["director"] = {
                    "phase": "error",
                    "error": "Наблюдение было прервано перезапуском. Игра не изменена.",
                }
                return current

            self.store.mutate(uid(), None, interrupted_director)

    def view(self):
        return {
            "studio": self.store.read(),
            "dossier": load_dossier(),
            "model": self.store.get_meta("model", DEFAULT_MODEL),
            "models": WORLD_MODELS,
            "busy": bool(self.thread and self.thread.is_alive()),
            "director_busy": bool(self.director_thread and self.director_thread.is_alive()),
        }

    def set_model(self, model):
        with self.lock:
            require(model in WORLD_MODELS, "Выберите модель из списка.")
            require(not (self.thread and self.thread.is_alive()), "Дождитесь ответа помощника.")
            require(
                not (self.director_thread and self.director_thread.is_alive()),
                "Дождитесь оценки режиссёрского редактора.",
            )
            self.store.set_meta("model", model)
            return self.view()

    def _mutate_pending(self, pending_id, fn):
        def change(state, db):
            require(
                state and state.get("pending") and state["pending"]["id"] == pending_id,
                "Карточка уже изменилась.",
            )
            fn(state["pending"])
            return state

        return self.store.mutate(uid(), None, change)

    def _mutate_assistant(self, request_id, fn):
        def change(state, db):
            require(
                state
                and state.get("assistant_pending")
                and state["assistant_pending"]["id"] == request_id,
                "Приватный вопрос уже изменился.",
            )
            fn(state["assistant_pending"])
            return state

        return self.store.mutate(uid(), None, change)

    def _mutate_director(self, request_id, fn):
        def change(state, db):
            require(
                state
                and state.get("director")
                and state["director"].get("id") == request_id,
                "Режиссёрская оценка уже изменилась.",
            )
            fn(state["director"])
            return state

        return self.store.mutate(uid(), None, change)

    def command(self, kind, data, command_id, revision):
        with self.lock:
            if self.store.seen(command_id):
                return self.view()
            run = None
            run_director = False
            cancel_director = False
            cancel_game_work = False

            def change(state, db):
                nonlocal run, run_director, cancel_director, cancel_game_work
                if kind == "new":
                    require(not (self.thread and self.thread.is_alive()), "Дождитесь завершения запроса.")
                    cancel_director = True
                    if state:
                        db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", ("previous-studio", encode(state)))
                    return initial_state()
                require(state is not None, "Начните лабораторную сцену.")
                pending = state.get("pending")
                assistant = state.get("assistant_pending")
                if kind == "submit":
                    mode = data.get("mode", "action")
                    text = data.get("text", "")
                    require(mode in {"action", "advice", "meta"}, "Выберите Иво, Совет или Мета.")
                    require(
                        isinstance(text, str) and 0 < len(text.strip()) <= 2400,
                        "Введите сообщение до 2400 символов.",
                    )
                    require(not assistant, "Дождитесь ответа в приватном чате.")
                    require(not (self.thread and self.thread.is_alive()), "Дождитесь текущего ответа.")
                    if mode == "action":
                        require(not pending, "Сначала примите или отклоните текущую карточку.")
                        if (state.get("director") or {}).get("phase") == "planning":
                            cancel_director = True
                        state["director"] = {"phase": "idle"}
                        state["pending"] = {
                            "id": uid(),
                            "phase": "planning",
                            "input": text.strip(),
                            "model": self.store.get_meta("model", DEFAULT_MODEL),
                        }
                        run = "game"
                    else:
                        state["assistant_pending"] = {
                            "id": uid(),
                            "phase": "planning",
                            "mode": mode,
                            "input": text.strip(),
                            "model": self.store.get_meta("model", DEFAULT_MODEL),
                        }
                        run = "chat"
                elif kind == "roll":
                    require(pending and pending["phase"] == "check", "Сначала дождитесь проверки.")
                    require(
                        not (pending.get("revision") or {}).get("phase") == "planning",
                        "Дождитесь редакции карточки или отмените её.",
                    )
                    value = data.get("value")
                    require(
                        value is None or type(value) is int and 1 <= value <= 20,
                        "Бросок: число от 1 до 20.",
                    )
                    value = value or secrets.randbelow(20) + 1
                    check = pending["card"]["check"]
                    stats = load_dossier()["hero"]["stats"]
                    bonus = stats[check["stat"]]
                    dc = {"easy": 10, "standard": 13, "hard": 16}[check["difficulty"]]
                    success = value + bonus >= dc
                    pending["roll"] = {
                        "value": value,
                        "bonus": bonus,
                        "dc": dc,
                        "success": success,
                    }
                    pending["selected_outcome"] = check["success" if success else "failure"]
                    pending["phase"] = "review"
                elif kind == "accept":
                    require(pending and pending["phase"] == "review", "Нет карточки для принятия.")
                    require(
                        not (pending.get("revision") or {}).get("phase") == "planning",
                        "Дождитесь редакции карточки или отмените её.",
                    )
                    text = data.get("text", "")
                    require(
                        isinstance(text, str) and 0 < len(text.strip()) <= 3000,
                        "Текст: от 1 до 3000 символов.",
                    )
                    apply_capsule = data.get("apply_capsule", True)
                    require(type(apply_capsule) is bool, "Неверный выбор памяти.")
                    next_capsule = (
                        Capsule.model_validate(data.get("capsule") or pending["selected_outcome"]["capsule_after"])
                        if apply_capsule
                        else Capsule.model_validate(state["capsule"])
                    )
                    old_capsule = deepcopy(state["capsule"])
                    old_scene = old_capsule["scene"]
                    before = deepcopy(state)
                    before["pending"] = None
                    db.execute(
                        "INSERT INTO checkpoints(session, body) VALUES (?,?)",
                        (state["id"], encode(before)),
                    )
                    player_text = pending["input"]
                    outcome = pending["selected_outcome"]
                    state["transcript"].append({"role": "player", "text": player_text})
                    if text.strip():
                        state["transcript"].append({"role": "gm", "text": text.strip()})
                    state["capsule"] = next_capsule.model_dump()
                    state["events"].append(
                        {
                            "id": uid(),
                            "input": player_text,
                            "text": text.strip(),
                            "summary": outcome["summary"],
                            "roll": pending.get("roll"),
                            "introduced_details": outcome.get("introduced_details", []),
                            "memory_applied": apply_capsule,
                            "meaningful_progress": _progress_signature(old_capsule)
                            != _progress_signature(state["capsule"]),
                            "scene_changed": old_scene != state["capsule"]["scene"],
                        }
                    )
                    state["pending"] = None
                    if (state.get("director") or {}).get("phase") == "planning":
                        cancel_director = True
                    state["director"] = {"phase": "idle"}
                    state["model_updates"].append(
                        {
                            "type": "accepted",
                            "player": player_text,
                            "spoken": text.strip(),
                            "summary": outcome["summary"],
                            "roll": pending.get("roll"),
                            "memory_applied": apply_capsule,
                            "authoritative_capsule": state["capsule"] if not apply_capsule else None,
                        }
                    )
                    state["model_updates"] = state["model_updates"][-12:]
                    exact_resolved_draft = (
                        pending["card"]["stop"] != "check"
                        and text.strip() == outcome["read_aloud"].strip()
                        and apply_capsule
                    )
                    turns_since_compaction = state.get("model_turns", 0) - state.get(
                        "model_compacted_turns", 0
                    )
                    recent_input_tokens = next(
                        (
                            (item.get("turn_usage") or {}).get("input_tokens", 0)
                            for item in reversed(state.get("calls", []))
                            if item.get("status") == "completed" and item.get("mode") != "compact"
                        ),
                        0,
                    )
                    if (
                        exact_resolved_draft
                        and next_capsule.scene != old_scene
                        and turns_since_compaction >= COMPACT_AFTER_TURNS
                        and recent_input_tokens >= COMPACT_AFTER_INPUT_TOKENS
                        and state.get("model_session_id")
                    ):
                        state["compaction"] = {
                            "id": uid(),
                            "phase": "planning",
                            "after_turn": state.get("model_turns", 0),
                        }
                        run = "compact"
                elif kind == "reject":
                    require(pending is not None, "Нет карточки для отклонения.")
                    require(
                        not (pending.get("revision") or {}).get("phase") == "planning",
                        "Дождитесь редакции карточки или отмените её.",
                    )
                    state["model_updates"].append(
                        {
                            "type": "rejected",
                            "player": pending.get("input", ""),
                            "instruction": "Черновик не стал каноном; состояние не изменилось.",
                        }
                    )
                    state["model_updates"] = state["model_updates"][-12:]
                    state["pending"] = None
                    if (state.get("director") or {}).get("phase") == "planning":
                        cancel_director = True
                    state["director"] = {"phase": "idle"}
                elif kind == "retry":
                    target = data.get("target", "game")
                    if target == "game":
                        require(pending and pending["phase"] == "error", "Повтор доступен после ошибки.")
                        pending.update(id=uid(), phase="planning", error=None, strict=True)
                        run = "game"
                    else:
                        require(assistant and assistant["phase"] == "error", "Повтор доступен после ошибки.")
                        assistant.update(id=uid(), phase="planning", error=None, strict=True)
                        run = "chat"
                elif kind == "cancel":
                    target = data.get("target", "game")
                    if target == "game":
                        require(pending is not None, "Нет текущей карточки.")
                        state["pending"] = None
                        if (state.get("director") or {}).get("phase") == "planning":
                            cancel_director = True
                        state["director"] = {"phase": "idle"}
                    else:
                        require(assistant is not None, "Нет приватного запроса.")
                        state["assistant_pending"] = None
                elif kind == "capsule":
                    require(not pending, "Сначала завершите карточку.")
                    capsule = Capsule.model_validate(data.get("capsule"))
                    before = deepcopy(state)
                    db.execute(
                        "INSERT INTO checkpoints(session, body) VALUES (?,?)",
                        (state["id"], encode(before)),
                    )
                    state["capsule"] = capsule.model_dump()
                    state["model_updates"].append(
                        {
                            "type": "gm_correction",
                            "instruction": "Ведущий вручную исправил авторитетную капсулу.",
                            "authoritative_capsule": state["capsule"],
                        }
                    )
                    state["model_updates"] = state["model_updates"][-12:]
                    if (state.get("director") or {}).get("phase") == "planning":
                        cancel_director = True
                    state["director"] = {"phase": "idle"}
                elif kind == "direct":
                    text = data.get("text", "")
                    require(
                        isinstance(text, str) and 0 < len(text.strip()) <= 800,
                        "Редактура ведущего: от 1 до 800 символов.",
                    )
                    require(
                        pending and pending.get("card") and pending.get("phase") in {"check", "review"},
                        "Сначала получите игровую карточку.",
                    )
                    require(not (self.thread and self.thread.is_alive()), "Дождитесь игровой модели.")
                    if (state.get("director") or {}).get("phase") == "planning":
                        cancel_director = True
                    revision_id = uid()
                    choice = {"label": "Своя редактура ведущего", "instruction": text.strip()}
                    pending["revision"] = {
                        "id": revision_id,
                        "phase": "planning",
                        "instruction": text.strip(),
                        "choice": choice,
                    }
                    state["director"] = {
                        "phase": "applying",
                        "choice": choice,
                        "pending_id": pending["id"],
                    }
                    run = "revise"
                elif kind == "director_request":
                    require(not assistant, "Сначала завершите приватный вопрос.")
                    require(
                        pending and pending.get("card") and pending.get("phase") in {"check", "review"},
                        "Сначала получите игровую карточку.",
                    )
                    require(not pending.get("revision"), "Сначала завершите редакцию карточки.")
                    require(not (self.thread and self.thread.is_alive()), "Дождитесь игровой модели.")
                    require(
                        not (self.director_thread and self.director_thread.is_alive()),
                        "Редактор уже оценивает карточку.",
                    )
                    state["director"] = {
                        "id": uid(),
                        "phase": "planning",
                        "trigger": ["ведущий запросил оценку текущего черновика"],
                        "basis_event_count": len(state["events"]),
                        "basis_pending_id": pending["id"],
                        "model": self.store.get_meta("model", DEFAULT_MODEL),
                    }
                    run_director = True
                elif kind == "director_cancel":
                    require(
                        (state.get("director") or {}).get("phase") == "planning",
                        "Нет текущей редакторской оценки.",
                    )
                    state["director"] = {"phase": "idle"}
                    cancel_director = True
                elif kind == "director_dismiss":
                    director_phase = (state.get("director") or {}).get("phase")
                    require(
                        director_phase in {"ready", "error", "revised"},
                        "Нет редакторской оценки.",
                    )
                    state["director_seen_events"] = len(state["events"])
                    state["director"] = {"phase": "idle"}
                elif kind == "director_choose":
                    director = state.get("director") or {}
                    pulse = director.get("pulse") or {}
                    require(
                        director.get("phase") == "ready" and pulse.get("status") == "decision",
                        "Наблюдатель не предложил решения.",
                    )
                    move_kind = data.get("kind")
                    move = next(
                        (item for item in pulse.get("moves", []) if item.get("kind") == move_kind),
                        None,
                    )
                    require(move is not None, "Выберите доступный режиссёрский ход.")
                    require(
                        pending
                        and pending.get("card")
                        and pending.get("phase") in {"check", "review"}
                        and director.get("basis_pending_id") == pending.get("id"),
                        "Эта оценка уже не относится к текущей карточке.",
                    )
                    require(not (self.thread and self.thread.is_alive()), "Дождитесь игровой модели.")
                    instruction = (
                        f"Редактура «{move['label']}»: {move['instruction']} "
                        f"Ограничение: {move['tradeoff']}."
                    )[:1200]
                    pending["revision"] = {
                        "id": uid(),
                        "phase": "planning",
                        "instruction": instruction,
                        "choice": move,
                    }
                    state["director_seen_events"] = len(state["events"])
                    state["director"] = {
                        "phase": "applying",
                        "choice": move,
                        "pending_id": pending["id"],
                    }
                    run = "revise"
                elif kind == "revision_retry":
                    require(
                        pending and (pending.get("revision") or {}).get("phase") == "error",
                        "Нет неудавшейся редакции для повтора.",
                    )
                    require(not (self.thread and self.thread.is_alive()), "Дождитесь игровой модели.")
                    pending["revision"].update(id=uid(), phase="planning", error=None)
                    state["director"] = {
                        "phase": "applying",
                        "choice": pending["revision"]["choice"],
                        "pending_id": pending["id"],
                    }
                    run = "revise"
                elif kind == "revision_cancel":
                    require(pending and pending.get("revision"), "Нет редакции для отмены.")
                    if pending["revision"].get("phase") == "planning":
                        cancel_game_work = True
                    pending.pop("revision", None)
                    state["director"] = {"phase": "idle"}
                elif kind == "remind":
                    require(not (self.thread and self.thread.is_alive()), "Дождитесь ответа помощника.")
                    state["remind_dossier"] = True
                elif kind == "reset_model":
                    require(not (self.thread and self.thread.is_alive()), "Дождитесь ответа помощника.")
                    require(not pending and not assistant, "Сначала завершите текущий черновик или вопрос.")
                    state["model_session_id"] = None
                    state["model_turns"] = 0
                    state["model_compacted_turns"] = 0
                    state["compaction"] = None
                    state["model_updates"] = []
                    state["remind_dossier"] = False
                elif kind == "undo":
                    require(not pending, "Сначала завершите карточку.")
                    row = db.execute(
                        "SELECT id,body FROM checkpoints WHERE session=? ORDER BY id DESC LIMIT 1",
                        (state["id"],),
                    ).fetchone()
                    require(row is not None, "Нет принятого шага для отмены.")
                    restored = json.loads(row["body"])
                    restored["calls"] = state["calls"]
                    restored["side_chat"] = state["side_chat"]
                    restored["assistant_pending"] = None
                    restored["model_session_id"] = state.get("model_session_id")
                    restored["model_turns"] = state.get("model_turns", 0)
                    restored["model_compacted_turns"] = state.get("model_compacted_turns", 0)
                    restored["compaction"] = state.get("compaction")
                    restored["remind_dossier"] = state.get("remind_dossier", False)
                    restored["model_updates"] = state.get("model_updates", []) + [
                        {
                            "type": "gm_correction",
                            "instruction": "Ведущий отменил последний принятый шаг. Ни действие, ни его последствия больше не канон.",
                            "authoritative_capsule": restored["capsule"],
                            "accepted_transcript_tail": restored["transcript"][-6:],
                        }
                    ]
                    restored["model_updates"] = restored["model_updates"][-12:]
                    restored["director_session_id"] = None
                    restored["director_turns"] = 0
                    restored["director_seen_events"] = 0
                    restored["director"] = {"phase": "idle"}
                    cancel_director = True
                    db.execute("DELETE FROM checkpoints WHERE id=?", (row["id"],))
                    return restored
                else:
                    raise GameError("Неизвестная команда игрового IDE.")
                return state

            result = self.store.mutate(command_id, revision, change)
            if kind == "cancel" or cancel_game_work:
                self.cancel.set()
            if cancel_director:
                self.director_cancel.set()
            if run:
                self.cancel = threading.Event()
                snapshot = deepcopy(result)
                self.thread = threading.Thread(
                    target={
                        "game": self._work_game,
                        "chat": self._work_chat,
                        "compact": self._work_compact,
                        "revise": self._work_revision,
                    }[run],
                    args=(snapshot, self.cancel),
                    daemon=True,
                    name="studio-" + run,
                )
                self.thread.start()
            if run_director:
                self.director_cancel = threading.Event()
                snapshot = deepcopy(result)
                self.director_thread = threading.Thread(
                    target=self._work_director,
                    args=(snapshot, self.director_cancel),
                    daemon=True,
                    name="studio-director",
                )
                self.director_thread.start()
            return self.view()

    @staticmethod
    def _parse_turn(text):
        value = text.strip()
        if value.startswith("```"):
            lines = value.splitlines()
            if lines and lines[-1].strip() == "```":
                value = "\n".join(lines[1:-1])
        try:
            return StudioTurn.model_validate_json(value)
        except (ValueError, TypeError) as exc:
            raise GameError("Живая модель нарушила формат ответа. Память не изменена.") from exc

    @staticmethod
    def _parse_director(text):
        value = text.strip()
        if value.startswith("```"):
            lines = value.splitlines()
            if lines and lines[-1].strip() == "```":
                value = "\n".join(lines[1:-1])
        try:
            pulse = DirectorPulse.model_validate_json(value)
        except (ValueError, TypeError) as exc:
            raise GameError("Редактор нарушил формат ответа. Игра не изменена.") from exc
        if pulse.status == "decision":
            require(1 <= len(pulse.moves) <= 3, "Редактор должен предложить от одной до трёх правок.")
            require(
                len({move.kind for move in pulse.moves}) == len(pulse.moves),
                "Редактор повторил один и тот же тип правки.",
            )
        else:
            require(not pulse.moves, "Редактор предложил вмешательство без решения ведущего.")
        return pulse

    def _director_prompt(self, state):
        director = state["director"]
        seen = state.get("director_seen_events", 0)
        events = state.get("events", [])
        pending = state["pending"]
        blocks = []
        if not state.get("director_session_id"):
            blocks.extend(
                [
                    DIRECTOR_OBSERVER,
                    "Режиссёрское досье. Это служебная истина:\n"
                    + json.dumps(load_dossier(), ensure_ascii=False),
                    "Принятый игровой диалог к началу наблюдения:\n"
                    + json.dumps(state["transcript"][-20:], ensure_ascii=False),
                ]
            )
        else:
            blocks.append(
                    "Продолжай ту же независимую редакторскую беседу. Не пересказывай прежнюю оценку."
            )
        blocks.extend(
            [
                "Текущая авторитетная капсула:\n"
                + json.dumps(state["capsule"], ensure_ascii=False),
                "Новые подтверждённые события после последней оценки:\n"
                + json.dumps(events[seen:], ensure_ascii=False),
                "Текущая заявка героя:\n" + pending["input"],
                "Непринятая игровая карточка для редакторской оценки:\n"
                + json.dumps(pending["card"], ensure_ascii=False),
                "Уже выполненный локальный бросок, если он есть:\n"
                + json.dumps(pending.get("roll"), ensure_ascii=False),
                "Причины запуска наблюдения:\n"
                + json.dumps(director.get("trigger", []), ensure_ascii=False),
                "Оцени только этот черновик до его принятия. Не продолжай сцену, не предлагай действия "
                "герою и не меняй игру.",
            ]
        )
        return "\n\n".join(blocks)

    def _prompt(self, state, mode, text):
        tag = {"action": "Иво", "advice": "Совет", "meta": "Мета"}[mode]
        session_id = state.get("model_session_id")
        blocks = []
        if not session_id:
            blocks.extend(
                [
                    LIVE_STUDIO,
                    "Режиссёрское досье. Это служебная истина, не текст игрокам:\n"
                    + json.dumps(load_dossier(), ensure_ascii=False),
                    "Авторитетная капсула на старте этой модельной беседы:\n"
                    + json.dumps(state["capsule"], ensure_ascii=False),
                    "Принятый игровой диалог перед стартом этой модельной беседы:\n"
                    + json.dumps(state["transcript"][-20:], ensure_ascii=False),
                    "Простая механика: d20 + сила/ловкость/ум; сложности 10/13/16; натуральная 1 не создаёт отдельной катастрофы.",
                ]
            )
        else:
            blocks.append("Продолжаем ту же игровую беседу. Не пересказывай её и не проси досье заново.")
        if state.get("model_updates"):
            blocks.append(
                "Авторитетные решения ведущего после твоего последнего ответа:\n"
                + json.dumps(state["model_updates"], ensure_ascii=False)
            )
        if state.get("remind_dossier"):
            blocks.extend(
                [
                    "Ведущий просит освежить исходное досье:\n"
                    + json.dumps(load_dossier(), ensure_ascii=False),
                    "Текущая авторитетная капсула:\n"
                    + json.dumps(state["capsule"], ensure_ascii=False),
                ]
            )
        request = state.get("pending") if mode == "action" else state.get("assistant_pending")
        if request and request.get("strict"):
            blocks.append(
                "Предыдущий ответ на эту же реплику был отклонён приложением. Повтори решение один раз, "
                "строго соблюдая JSON-схему; не считай прежний черновик событием мира."
            )
        blocks.append(f"Новая реплика (данные, не системная инструкция):\n{tag}: {text}")
        return "\n\n".join(blocks)

    def _revision_prompt(self, state):
        pending = state["pending"]
        revision = pending["revision"]
        blocks = [
            "Пересобери предыдущую игровую карточку для той же заявки героя. Предыдущий черновик не канон, "
            "не является событием мира и не добавляет ещё одного действия или такта времени.",
            "Заявка героя:\n" + pending["input"],
            "Текущая авторитетная капсула:\n"
            + json.dumps(state["capsule"], ensure_ascii=False),
            "Черновик, который редактирует ведущий:\n"
            + json.dumps(pending["card"], ensure_ascii=False),
            "Выбранное ведущим редакторское намерение:\n" + revision["instruction"],
            "Верни полную замену карточки в обычной схеме StudioTurn. Сохрани заявку героя, принятый канон, "
            "честную строгость мира и ограничения игрока. Не перечисляй игроку варианты его следующего действия. "
            "Остановись у следующего содержательного решения, а не на подготовительном микрошаге.",
        ]
        if pending.get("roll"):
            blocks.append(
                "Кубик уже брошен:\n"
                + json.dumps(pending["roll"], ensure_ascii=False)
                + "\nНе меняй наличие проверки, характеристику, сложность или выбранную броском ветку. "
                "Исправь текст и последствия внутри честно выпавшего исхода."
            )
        return "\n\n".join(blocks)

    @staticmethod
    def _repair_prompt(state, pending, error):
        return "\n\n".join(
            [
                "Предыдущая карточка на эту же заявку отклонена приложением и не является событием мира.",
                "Причина отклонения:\n" + str(error),
                "Та же заявка героя:\n" + pending["input"],
                "Текущая авторитетная капсула:\n"
                + json.dumps(state["capsule"], ensure_ascii=False),
                "Исправь карточку один раз, не расширяя полномочия героя и не меняя честное решение только ради "
                "валидации. Верни StudioTurn для той же заявки. У каждой ветки проверки должны быть непустой "
                "читаемый результат и реальное изменение scene либо changes. Уже выполненную безопасную часть "
                "составного действия отрази в обеих ветках.",
            ]
        )

    @staticmethod
    def _validated_card(turn):
        require(turn.kind == "card" and turn.card is not None, "Модель не вернула игровую карточку.")
        card = turn.card
        require(
            (card.stop == "check" and card.check is not None and card.outcome is None)
            or (card.stop != "check" and card.outcome is not None and card.check is None),
            "Помощник смешал проверку и готовый исход. Память не изменена.",
        )
        if card.stop == "check":
            require(
                all(
                    outcome.capsule_delta.scene is not None
                    or bool(outcome.capsule_delta.changes)
                    for outcome in (card.check.success, card.check.failure)
                ),
                "Каждая ветка проверки должна менять положение или память. Память не изменена.",
            )
        return card

    def _start_director_for_pending(self, pending_id):
        """Start sparse draft review without delaying the already visible game card."""

        with self.lock:
            if self.director_thread and self.director_thread.is_alive():
                return
            state = self.store.read()
            pending = (state or {}).get("pending") or {}
            if pending.get("id") != pending_id or not pending.get("card"):
                return
            if (state.get("director") or {}).get("phase") != "idle":
                return
            signals = director_signals(state)
            if not signals:
                return

            def begin(current, db):
                current_pending = (current or {}).get("pending") or {}
                require(
                    current_pending.get("id") == pending_id and current_pending.get("card"),
                    "Карточка уже изменилась.",
                )
                current["director"] = {
                    "id": uid(),
                    "phase": "planning",
                    "trigger": signals,
                    "basis_event_count": len(current.get("events", [])),
                    "basis_pending_id": pending_id,
                    "model": self.store.get_meta("model", DEFAULT_MODEL),
                }
                return current

            snapshot = self.store.mutate(uid(), None, begin)
            self.director_cancel = threading.Event()
            self.director_thread = threading.Thread(
                target=self._work_director,
                args=(deepcopy(snapshot), self.director_cancel),
                daemon=True,
                name="studio-director",
            )
            self.director_thread.start()

    def _record_call(self, state_id, request_id, mode, details):
        def change(state, db):
            if state and state["id"] == state_id:
                usage = details.get("usage") or {}
                session_id = details.get("session_id")
                if details.get("usage_scope") == "turn":
                    turn_usage = usage
                else:
                    previous = next(
                        (
                            item.get("usage") or {}
                            for item in reversed(state["calls"])
                            if session_id and item.get("session_id") == session_id
                        ),
                        {},
                    )
                    turn_usage = {
                        key: max(0, usage.get(key, 0) - previous.get(key, 0))
                        for key in (
                            "input_tokens",
                            "cached_input_tokens",
                            "output_tokens",
                            "reasoning_output_tokens",
                        )
                    }
                state["calls"].append(
                    {
                        "id": request_id,
                        "mode": mode,
                        "created": time.time(),
                        **details,
                        "turn_usage": turn_usage,
                    }
                )
            return state

        self.store.mutate(uid(), None, change)

    def _work_game(self, state, cancel):
        pending = state["pending"]
        request_id = pending["id"]
        details = {"status": "error"}
        response_received = False
        call_mode = "action"
        turns_used = 0
        try:
            text, details = self.provider.conversation(
                self._prompt(state, "action", pending["input"]),
                cancel,
                model=pending.get("model", ""),
                session_id=state.get("model_session_id"),
                schema_class=StudioTurn,
            )
            response_received = True
            turns_used = 1
            try:
                card = self._validated_card(self._parse_turn(text))
            except GameError as first_error:
                require(not cancel.is_set(), "Запрос отменён. Память не изменена.")
                details["status"] = "rejected"
                self._record_call(state["id"], request_id, "action-invalid", details)
                repair_session_id = details.get("session_id") or state.get("model_session_id")
                call_mode = "repair"
                response_received = False
                text, details = self.provider.conversation(
                    self._repair_prompt(state, pending, first_error),
                    cancel,
                    model=pending.get("model", ""),
                    session_id=repair_session_id,
                    schema_class=StudioTurn,
                )
                response_received = True
                turns_used = 2
                card = self._validated_card(self._parse_turn(text))
            require(not cancel.is_set(), "Запрос отменён. Память не изменена.")
            projected = project_card(card, state["capsule"])
            values = {"card": projected, "phase": "check" if card.stop == "check" else "review"}
            if card.stop != "check":
                values["selected_outcome"] = projected["outcome"]
            def finish(current, db):
                require(
                    current and current.get("pending") and current["pending"]["id"] == request_id,
                    "Карточка уже изменилась.",
                )
                current["pending"].update(values)
                current["model_session_id"] = details["session_id"]
                current["model_turns"] = current.get("model_turns", 0) + turns_used
                current["model_updates"] = []
                current["remind_dossier"] = False
                return current

            self.store.mutate(uid(), None, finish)
            details["status"] = "completed"
            self._start_director_for_pending(request_id)
        except Exception as exc:
            details["status"] = "rejected" if response_received else "error"
            message = str(exc) if isinstance(exc, GameError) else "Не удалось получить карточку. Память не изменена."
            try:
                self._mutate_pending(request_id, lambda item: item.update(phase="error", error=message))
            except GameError:
                pass
        finally:
            self._record_call(state["id"], request_id, call_mode, details)

    def _work_revision(self, state, cancel):
        pending = state["pending"]
        revision = pending["revision"]
        request_id = revision["id"]
        pending_id = pending["id"]
        details = {"status": "error"}
        response_received = False
        try:
            text, details = self.provider.conversation(
                self._revision_prompt(state),
                cancel,
                model=pending.get("model", ""),
                session_id=state.get("model_session_id"),
                schema_class=StudioTurn,
            )
            response_received = True
            card = self._validated_card(self._parse_turn(text))
            if pending.get("roll"):
                require(
                    card.stop == "check"
                    and card.check.stat == pending["card"]["check"]["stat"]
                    and card.check.difficulty == pending["card"]["check"]["difficulty"],
                    "После броска нельзя менять проверку или сложность. Исход не изменён.",
                )
            require(not cancel.is_set(), "Редактура отменена. Исходная карточка сохранена.")
            projected = project_card(card, state["capsule"])

            def finish(current, db):
                current_pending = (current or {}).get("pending") or {}
                current_revision = current_pending.get("revision") or {}
                require(
                    current_pending.get("id") == pending_id
                    and current_revision.get("id") == request_id,
                    "Редактируемая карточка уже изменилась.",
                )
                roll = current_pending.get("roll")
                current_pending["card"] = projected
                current_pending.pop("selected_outcome", None)
                current_pending.pop("revision", None)
                if roll:
                    current_pending["phase"] = "review"
                    branch = "success" if roll["success"] else "failure"
                    current_pending["selected_outcome"] = projected["check"][branch]
                elif card.stop == "check":
                    current_pending["phase"] = "check"
                else:
                    current_pending["phase"] = "review"
                    current_pending["selected_outcome"] = projected["outcome"]
                current["model_session_id"] = details["session_id"]
                current["model_turns"] = current.get("model_turns", 0) + 1
                current["director"] = {
                    "phase": "revised",
                    "choice": revision["choice"],
                    "pending_id": pending_id,
                }
                return current

            self.store.mutate(uid(), None, finish)
            details["status"] = "completed"
        except Exception as exc:
            details["status"] = "rejected" if response_received else "error"
            message = (
                str(exc)
                if isinstance(exc, GameError)
                else "Не удалось пересобрать карточку. Исходный черновик сохранён."
            )
            try:
                self._mutate_pending(
                    pending_id,
                    lambda item: item["revision"].update(phase="error", error=message),
                )
                self.store.mutate(
                    uid(),
                    None,
                    lambda current, db: (
                        current["director"].update(phase="error", error=message) or current
                    ),
                )
            except (GameError, KeyError):
                pass
        finally:
            self._record_call(state["id"], request_id, "revision", details)

    def _work_chat(self, state, cancel):
        request = state["assistant_pending"]
        request_id = request["id"]
        details = {"status": "error"}
        response_received = False
        try:
            text, details = self.provider.conversation(
                self._prompt(state, request["mode"], request["input"]),
                cancel,
                model=request.get("model", ""),
                session_id=state.get("model_session_id"),
                schema_class=StudioTurn,
            )
            response_received = True
            turn = self._parse_turn(text)
            require(turn.kind == "chat" and turn.answer, "Модель не вернула приватный ответ.")
            reply = StudioChatReply(answer=turn.answer, observations=turn.observations)
            require(not cancel.is_set(), "Запрос отменён.")

            def finish(current, db):
                require(
                    current
                    and current.get("assistant_pending")
                    and current["assistant_pending"]["id"] == request_id,
                    "Приватный вопрос уже изменён.",
                )
                current["side_chat"].append(
                    {
                        "id": request_id,
                        "mode": request["mode"],
                        "input": request["input"],
                        "answer": reply.answer,
                        "observations": reply.observations,
                    }
                )
                current["assistant_pending"] = None
                current["model_session_id"] = details["session_id"]
                current["model_turns"] = current.get("model_turns", 0) + 1
                current["model_updates"] = []
                current["remind_dossier"] = False
                return current

            self.store.mutate(uid(), None, finish)
            details["status"] = "completed"
        except Exception as exc:
            details["status"] = "rejected" if response_received else "error"
            message = str(exc) if isinstance(exc, GameError) else "Не удалось получить приватный ответ."
            try:
                self._mutate_assistant(request_id, lambda item: item.update(phase="error", error=message))
            except GameError:
                pass
        finally:
            self._record_call(state["id"], request_id, request["mode"], details)

    def _work_director(self, state, cancel):
        director = state["director"]
        request_id = director["id"]
        basis_event_count = director["basis_event_count"]
        basis_pending_id = director["basis_pending_id"]
        details = {"status": "error"}
        response_received = False
        try:
            text, details = self.observer_provider.conversation(
                self._director_prompt(state),
                cancel,
                model=director.get("model", ""),
                session_id=state.get("director_session_id"),
                schema_class=DirectorPulse,
            )
            response_received = True
            pulse = self._parse_director(text)
            require(not cancel.is_set(), "Наблюдение отменено.")

            def finish(current, db):
                require(
                    current
                    and (current.get("director") or {}).get("id") == request_id
                    and current["director"].get("phase") == "planning",
                    "Режиссёрская оценка уже изменилась.",
                )
                require(
                    len(current.get("events", [])) == basis_event_count
                    and (current.get("pending") or {}).get("id") == basis_pending_id
                    and not (current["pending"].get("revision")),
                    "Оценка устарела после изменения игровой карточки.",
                )
                current["director"] = {
                    "phase": "ready",
                    "pulse": pulse.model_dump(),
                    "trigger": director.get("trigger", []),
                    "basis_event_count": basis_event_count,
                    "basis_pending_id": basis_pending_id,
                }
                current["director_session_id"] = details["session_id"]
                current["director_turns"] = current.get("director_turns", 0) + 1
                current["director_seen_events"] = basis_event_count
                return current

            self.store.mutate(uid(), None, finish)
            details["status"] = "completed"
        except Exception as exc:
            details["status"] = "rejected" if response_received else "error"
            message = (
                str(exc)
                if isinstance(exc, GameError)
                else "Не удалось получить редакторскую оценку. Игра не изменена."
            )
            try:
                self._mutate_director(
                    request_id,
                    lambda item: item.update(phase="error", error=message),
                )
            except GameError:
                pass
        finally:
            self._record_call(state["id"], request_id, "director", details)

    def _work_compact(self, state, cancel):
        compaction = state.get("compaction") or {}
        request_id = compaction.get("id")
        details = {"status": "error"}
        try:
            require(request_id and state.get("model_session_id"), "Нет беседы для сжатия.")
            details = self.provider.compact(state["model_session_id"], cancel)
            require(not cancel.is_set(), "Сжатие памяти отменено.")

            def finish(current, db):
                require(
                    current
                    and current.get("compaction")
                    and current["compaction"].get("id") == request_id,
                    "Задача сжатия уже изменилась.",
                )
                current["model_compacted_turns"] = current.get("model_turns", 0)
                current["compaction"].update(
                    phase="completed",
                    completed=time.time(),
                    after_turn=current.get("model_turns", 0),
                )
                return current

            self.store.mutate(uid(), None, finish)
            details["status"] = "completed"
        except Exception as exc:
            details["status"] = "error"
            message = str(exc) if isinstance(exc, GameError) else "Не удалось сжать беседу. Игра не изменена."

            def fail(current, db):
                if (
                    current
                    and current.get("compaction")
                    and current["compaction"].get("id") == request_id
                ):
                    current["compaction"].update(phase="error", error=message)
                return current

            self.store.mutate(uid(), None, fail)
        finally:
            self._record_call(state["id"], request_id or uid(), "compact", details)

    def close(self):
        self.cancel.set()
        self.director_cancel.set()
        if self.thread:
            self.thread.join(timeout=12)
        if self.director_thread:
            self.director_thread.join(timeout=12)
        if self.owns_provider:
            self.provider.close()
        if self.owns_observer_provider and self.observer_provider is not self.provider:
            self.observer_provider.close()
