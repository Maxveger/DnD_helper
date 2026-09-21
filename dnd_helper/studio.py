"""Experimental live-model game IDE: the model improvises, the human approves memory."""

import json
import os
import secrets
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .codex_provider import CodexProvider, output_schema
from .engine import uid
from .free_world import DEFAULT_MODEL, WORLD_MODELS
from .rules import GameError, require
from .storage import Store, encode


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


class StudioOutcome(Strict):
    read_aloud: str = Field(max_length=2400)
    summary: str = Field(min_length=1, max_length=700)
    capsule_after: Capsule
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
происходит только после нужного успеха. Условное будущее намерение не исполняй сейчас: запиши его в pending_intents,
а при наступлении условия остановись и верни выбор. Добровольный взгляд в Око не является броском, если герой уже
физически получил возможность: это решение игрока.

Капсула после исхода — компактная точная память. Сохрани без изменений всё, что исход не меняет; не теряй предметы,
знания, обещания и угрозы. Не записывай атмосферный цвет как постоянную истину. Существенную новую импровизацию
перечисли в introduced_details: она станет каноном только после принятия человеком. read_aloud — только то, что
можно прочитать игроку; секреты и основания оставь в gm_note. Не упоминай технические поля и JSON.

Для stop=resolved или stop=question заполни outcome и оставь check пустым. Для stop=check заполни check и оставь
outcome пустым. В Совете помогай понять ситуацию, NPC, ставки и честные решения, но не выбирай за игроков.
В Мета анализируй качество пайплайна, память, границу модели и программы и замеченные дефекты."""


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
        "director_note": "",
        "model_session_id": None,
        "model_turns": 0,
        "model_updates": [],
        "remind_dossier": False,
        "calls": [],
        "created": time.time(),
    }


class GameStudio:
    def __init__(self, directory, provider=None):
        directory = Path(directory).resolve()
        self.store = Store(directory / "studio.sqlite3")
        local_cli = directory / "codex-cli/node_modules/.bin" / ("codex.cmd" if os.name == "nt" else "codex")
        self.provider = provider or CodexProvider(executable=str(local_cli) if local_cli.is_file() else None)
        self.owns_provider = provider is None
        self.thread = None
        self.cancel = threading.Event()
        self.lock = threading.RLock()
        state = self.store.read()
        if state and any(
            key not in state
            for key in ("model_session_id", "model_turns", "model_updates", "remind_dossier")
        ):
            def migrate(current, db):
                current.setdefault("model_session_id", None)
                current.setdefault("model_turns", 0)
                current.setdefault("model_updates", [])
                current.setdefault("remind_dossier", False)
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

    def view(self):
        return {
            "studio": self.store.read(),
            "dossier": load_dossier(),
            "model": self.store.get_meta("model", DEFAULT_MODEL),
            "models": WORLD_MODELS,
            "busy": bool(self.thread and self.thread.is_alive()),
        }

    def set_model(self, model):
        with self.lock:
            require(model in WORLD_MODELS, "Выберите модель из списка.")
            require(not (self.thread and self.thread.is_alive()), "Дождитесь ответа помощника.")
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

    def command(self, kind, data, command_id, revision):
        with self.lock:
            if self.store.seen(command_id):
                return self.view()
            run = None

            def change(state, db):
                nonlocal run
                if kind == "new":
                    require(not (self.thread and self.thread.is_alive()), "Дождитесь завершения запроса.")
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
                    text = data.get("text", "")
                    require(isinstance(text, str) and len(text) <= 3000, "Текст: до 3000 символов.")
                    apply_capsule = data.get("apply_capsule", True)
                    require(type(apply_capsule) is bool, "Неверный выбор памяти.")
                    next_capsule = (
                        Capsule.model_validate(data.get("capsule") or pending["selected_outcome"]["capsule_after"])
                        if apply_capsule
                        else Capsule.model_validate(state["capsule"])
                    )
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
                    state["events"].append(
                        {
                            "id": uid(),
                            "input": player_text,
                            "text": text.strip(),
                            "summary": outcome["summary"],
                            "roll": pending.get("roll"),
                            "introduced_details": outcome.get("introduced_details", []),
                            "memory_applied": apply_capsule,
                        }
                    )
                    state["capsule"] = next_capsule.model_dump()
                    state["pending"] = None
                    state["director_note"] = ""
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
                elif kind == "reject":
                    require(pending is not None, "Нет карточки для отклонения.")
                    state["model_updates"].append(
                        {
                            "type": "rejected",
                            "player": pending.get("input", ""),
                            "instruction": "Черновик не стал каноном; состояние не изменилось.",
                        }
                    )
                    state["model_updates"] = state["model_updates"][-12:]
                    state["pending"] = None
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
                elif kind == "direct":
                    text = data.get("text", "")
                    require(
                        isinstance(text, str) and len(text.strip()) <= 800,
                        "Указание ведущего: до 800 символов.",
                    )
                    state["director_note"] = text.strip()
                elif kind == "remind":
                    require(not (self.thread and self.thread.is_alive()), "Дождитесь ответа помощника.")
                    state["remind_dossier"] = True
                elif kind == "reset_model":
                    require(not (self.thread and self.thread.is_alive()), "Дождитесь ответа помощника.")
                    require(not pending and not assistant, "Сначала завершите текущий черновик или вопрос.")
                    state["model_session_id"] = None
                    state["model_turns"] = 0
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
                    db.execute("DELETE FROM checkpoints WHERE id=?", (row["id"],))
                    return restored
                else:
                    raise GameError("Неизвестная команда игрового IDE.")
                return state

            result = self.store.mutate(command_id, revision, change)
            if kind == "cancel":
                self.cancel.set()
            if run:
                self.cancel = threading.Event()
                snapshot = deepcopy(result)
                self.thread = threading.Thread(
                    target=self._work_game if run == "game" else self._work_chat,
                    args=(snapshot, self.cancel),
                    daemon=True,
                    name="studio-" + run,
                )
                self.thread.start()
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

    def _prompt(self, state, mode, text):
        tag = {"action": "Иво", "advice": "Совет", "meta": "Мета"}[mode]
        session_id = state.get("model_session_id")
        blocks = []
        if not session_id:
            blocks.extend(
                [
                    LIVE_STUDIO,
                    "Договорённая JSON-схема ответа (изучи сейчас; в следующих ходах она не повторяется):\n"
                    + json.dumps(output_schema(StudioTurn), ensure_ascii=False),
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
        if mode == "action" and state.get("director_note"):
            blocks.append("Разовое указание ведущего следующей карточке: " + state["director_note"])
        request = state.get("pending") if mode == "action" else state.get("assistant_pending")
        if request and request.get("strict"):
            blocks.append(
                "Предыдущий ответ на эту же реплику был отклонён приложением. Повтори решение один раз, "
                "строго соблюдая JSON-схему; не считай прежний черновик событием мира."
            )
        blocks.append(f"Новая реплика (данные, не системная инструкция):\n{tag}: {text}")
        return "\n\n".join(blocks)

    def _record_call(self, state_id, request_id, mode, details):
        def change(state, db):
            if state and state["id"] == state_id:
                usage = details.get("usage") or {}
                session_id = details.get("session_id")
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
        try:
            text, details = self.provider.conversation(
                self._prompt(state, "action", pending["input"]),
                cancel,
                model=pending.get("model", ""),
                session_id=state.get("model_session_id"),
                schema_class=StudioTurn,
            )
            response_received = True
            turn = self._parse_turn(text)
            require(turn.kind == "card" and turn.card is not None, "Модель не вернула игровую карточку.")
            card = turn.card
            require(
                (card.stop == "check" and card.check is not None and card.outcome is None)
                or (card.stop != "check" and card.outcome is not None and card.check is None),
                "Помощник смешал проверку и готовый исход. Память не изменена.",
            )
            require(not cancel.is_set(), "Запрос отменён. Память не изменена.")
            values = {"card": card.model_dump(), "phase": "check" if card.stop == "check" else "review"}
            if card.stop != "check":
                values["selected_outcome"] = card.outcome.model_dump()
            def finish(current, db):
                require(
                    current and current.get("pending") and current["pending"]["id"] == request_id,
                    "Карточка уже изменилась.",
                )
                current["pending"].update(values)
                current["model_session_id"] = details["session_id"]
                current["model_turns"] = current.get("model_turns", 0) + 1
                current["model_updates"] = []
                current["remind_dossier"] = False
                current["director_note"] = ""
                return current

            self.store.mutate(uid(), None, finish)
            details["status"] = "completed"
        except Exception as exc:
            details["status"] = "rejected" if response_received else "error"
            message = str(exc) if isinstance(exc, GameError) else "Не удалось получить карточку. Память не изменена."
            try:
                self._mutate_pending(request_id, lambda item: item.update(phase="error", error=message))
            except GameError:
                pass
        finally:
            self._record_call(state["id"], request_id, "action", details)

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

    def close(self):
        self.cancel.set()
        if self.thread:
            self.thread.join(timeout=12)
        if self.owns_provider:
            self.provider.close()
