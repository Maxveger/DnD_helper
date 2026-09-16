"""Game commands shared by web and Telegram. No knowledge of HTTP or API keys."""

import secrets
import time
from copy import deepcopy

from .content import ADVENTURE, CHARACTERS, characters
from .adventures import catalog_for, character_catalog
from .declarative import DeclarativeRules
from .rules import GameError, SimpleRules, require
from .storage import encode


def uid():
    return secrets.token_hex(8)


def log(state, title, text, kind="event"):
    state["journal"].append({"id": uid(), "time": time.time(), "title": title, "text": text, "kind": kind})


def new_state(ids, demo, rules=None, definition=None):
    rules = DeclarativeRules(definition) if definition else (rules or SimpleRules())
    available = character_catalog(definition) if definition else CHARACTERS
    minimum, maximum = (definition["min_players"], definition["max_players"]) if definition else (1, 2)
    start = definition["start"] if definition else "reception"
    require(
        isinstance(ids, list)
        and minimum <= len(ids) <= maximum
        and all(isinstance(i, str) for i in ids)
        and len(set(ids)) == len(ids)
        and all(i in available for i in ids),
        f"Выберите от {minimum} до {maximum} разных персонажей.",
    )
    require(type(demo) is bool, "Режим игры должен быть выбран явно.")
    return {
        "id": uid(),
        "revision": 0,
        "ruleset": rules.id,
        "adventure": definition["id"] + "@" + definition["version"] if definition else ADVENTURE,
        **({"definition": deepcopy(definition)} if definition else {}),
        "status": "lobby",
        "demo": demo,
        "scene": start,
        "created": time.time(),
        "characters": {k: {**available[k], "hp": available[k]["max_hp"], "location": start} for k in ids}
        if definition
        else characters(ids),
        "clues": [],
        "queue": [],
        "pending": None,
        "participants": {},
        "invite": secrets.token_urlsafe(18),
        "gm_invite": secrets.token_urlsafe(18),
        "invite_expires": time.time() + 86400,
        "journal": [],
        "world": rules.initial_world(len(ids)),
    }


class Engine:
    def __init__(self, store, roller=None, rules=None, adventures=None):
        self.store = store
        self.adventures = adventures
        self.custom_roller = roller
        self.rules = rules or SimpleRules()
        self.roller = roller or (lambda: secrets.randbelow(20) + 1)

    def command(self, kind, data, command_id=None, expected=None):
        if kind in {"new", "finish"} and self.store.read():
            self.store.backup()

        def change(state, db):
            for field in ("actor", "intent", "scene", "target"):
                if field in data:
                    require(data[field] is None or isinstance(data[field], str), "Неверный формат действия.")
            if kind == "new":
                key = data.get("adventure_key", "builtin")
                require(key == "builtin" or self.adventures is not None, "Библиотека приключений недоступна.")
                definition = self.adventures.get(key) if key != "builtin" else None
                default = [next(iter(definition["characters"]))] if definition else ["mira"]
                return new_state(
                    data.get("characters", default), data.get("demo", True), self.rules, definition
                )
            require(state is not None, "Сначала создайте игру.")
            rules = self.rules_for(state)
            cat = catalog_for(state)
            require(state["ruleset"] == rules.id, "Для сохранения требуется другой модуль правил.")
            if kind == "join":
                self.join(state, data)
                return state
            if kind == "start":
                require(state["status"] == "lobby", "Игра уже началась.")
                if not state["demo"]:
                    bound = {p.get("actor") for p in state["participants"].values() if p["role"] == "player"}
                    require(bound == set(state["characters"]), "Дождитесь подключения выбранных игроков.")
                state["status"] = "active"
                log(state, "Приключение начинается", cat["scenes"][state["scene"]]["text"])
            elif kind == "pause":
                require(state["status"] in {"active", "paused"}, "Сначала начните игру.")
                state["status"] = "paused" if state["status"] == "active" else "active"
            elif kind in {"submit", "choose"}:
                require(state["status"] in {"active", "paused"}, "Действия доступны после начала игры.")
                if kind == "choose":
                    require(state["status"] == "active", "Возобновите игру.")
                    require(
                        state["pending"] is None or state["pending"]["phase"] == "clarify",
                        "Сначала завершите текущее действие.",
                    )
                require(data.get("actor") in state["characters"], "Выберите персонажа.")
                if data.get("sender"):
                    participant = state["participants"].get(str(data["sender"]))
                    require(
                        participant
                        and (participant["role"] == "gm" or participant["actor"] == data["actor"]),
                        "Привязка участника изменилась. Подключитесь заново.",
                    )
                text = str(data.get("text", "")).strip()
                require(0 < len(text) <= 2000, "Действие должно содержать от 1 до 2000 символов.")
                require(
                    kind == "choose" or len(state["queue"]) < 30,
                    "Очередь заполнена. Сначала разберите текущие действия.",
                )
                intent = data.get("intent", "unknown")
                require(intent in cat["intents"], "Неизвестное действие.")
                action = {
                    "id": uid(),
                    "actor": data["actor"],
                    "text": text,
                    "intent": intent,
                    "target": data.get("target"),
                    "source": data.get("source", "web"),
                    "sender": data.get("sender"),
                }
                if kind == "choose":
                    plan = self.prepare(state, action)
                    require(plan["phase"] != "clarify", plan["text"])
                    if state["pending"]:
                        log(state, "Действие заменено", state["pending"]["action"]["text"], "system")
                    state["pending"] = plan
                else:
                    state["queue"].append(action)
            elif kind == "clear_actions":
                require(
                    state["pending"] is None or state["pending"]["phase"] == "clarify",
                    "Сначала завершите текущее действие.",
                )
                count = len(state["queue"]) + bool(state["pending"])
                require(count > 0, "Нет действий для отмены.")
                log(
                    state,
                    "Действия отменены ведущим",
                    f"Отменено заявок: {count}. Игровой мир не изменён.",
                    "system",
                )
                state["pending"] = None
                state["queue"] = []
            elif kind == "discard":
                require(state["pending"] is not None, "Нет текущего действия.")
                log(state, "Действие пропущено", state["pending"]["action"]["text"], "system")
                state["pending"] = None
            elif kind == "edit":
                p = self.pending(state, {"check", "ready", "clarify"})
                require(not p.get("roll"), "Бросок уже сделан. Сначала отмените шаг.")
                action = deepcopy(p["action"])
                action.update({k: data[k] for k in ("text", "intent", "target") if k in data})
                state["pending"] = self.prepare(state, action)
            elif kind == "model_plan":
                p = self.pending(state, {"clarify"})
                require(p["id"] == data["pending_id"], "Действие уже изменилось.")
                require(data["intent"] in cat["intents"], "Модель предложила неизвестное действие.")
                require(
                    data.get("target") is None or data["target"] in state["characters"], "Неизвестная цель."
                )
                action = {**p["action"], "intent": data["intent"], "target": data.get("target")}
                state["pending"] = self.prepare(state, action)
                state["pending"]["ai_done"] = True
            elif kind == "narrate":
                p = self.pending(state, {"ready"})
                require(p["id"] == data["pending_id"], "Действие уже изменилось.")
                require(
                    0 < len(data["text"]) <= 1500 and len(data["prompt"]) <= 400, "Ответ слишком длинный."
                )
                p.setdefault("original_text", p["text"])
                p.update(
                    text=data["text"],
                    prompt=data["prompt"],
                    note="Формулировка GPT; механический исход сохранён.",
                )
            elif kind == "request_roll":
                p = self.pending(state, {"check"})
                p.update(phase="waiting", request_id=uid())
            elif kind == "roll":
                p = self.pending(state, {"waiting", "ready"})
                if data.get("user_id"):
                    participant = state["participants"].get(str(data["user_id"]))
                    require(
                        participant
                        and participant["role"] == "player"
                        and participant["actor"] == p["action"]["actor"],
                        "Этот бросок предназначен другому участнику.",
                    )
                require(
                    p.get("request_id") == data.get("request_id"), "Этот запрос броска больше не действует."
                )
                if not p.get("roll"):
                    die = data.get("die")
                    source = "physical" if die is not None else "app"
                    dice = p["check"].get("dice", 20)
                    generated = self.custom_roller or (lambda: secrets.randbelow(dice) + 1)
                    p = rules.roll(p, generated() if die is None else die, source)
                    state["pending"] = p
            elif kind == "manual":
                require(state["status"] == "active", "Возобновите игру.")
                if data.get("user_id"):
                    require(
                        state["participants"].get(str(data["user_id"]), {}).get("role") == "gm",
                        "Нужна роль ведущего.",
                    )
                text = str(data.get("text", "")).strip()
                require(0 < len(text) <= 3000, "Опишите результат: до 3000 символов.")
                actor = data.get("actor") or next(iter(state["characters"]))
                require(actor in state["characters"], "Неизвестный персонаж.")
                hp = data.get("hp", {})
                require(
                    isinstance(hp, dict)
                    and all(
                        k in state["characters"]
                        and type(v) is int
                        and 0 <= v <= state["characters"][k]["max_hp"]
                        for k, v in hp.items()
                    ),
                    "HP должны быть от 0 до максимума персонажа.",
                )
                old = state["pending"]
                action = (
                    old["action"]
                    if old
                    else {"id": uid(), "actor": actor, "text": "Решение ведущего", "intent": "unknown"}
                )
                # Manual outcomes are explicit and audited, never inferred from a model's prose.
                state["pending"] = {
                    "id": uid(),
                    "action": action,
                    "title": "Решение ведущего",
                    "phase": "ready",
                    "text": text,
                    "prompt": "Что делаете дальше?",
                    "note": "Исход задан ведущим.",
                    "roll": old.get("roll") if old else None,
                    "check": old.get("check") if old else None,
                    "effects": [],
                    "changes": [f"{state['characters'][k]['name']}: {v} HP" for k, v in hp.items()],
                    "manual_hp": hp,
                    "in_combat": state["world"]["combat"],
                }
                if state["world"]["combat"]:
                    state["pending"]["changes"].append("Бой будет завершён ручным решением")
            elif kind == "accept":
                p = self.pending(state, {"ready"})
                snapshot = deepcopy(state)
                snapshot["pending"] = None
                db.execute(
                    "INSERT INTO checkpoints(session, body) VALUES (?, ?)", (state["id"], encode(snapshot))
                )
                if "manual_hp" in p:
                    rules.manual(state, p["manual_hp"])
                    extra = []
                else:
                    extra = rules.apply(state, p, self.roller)
                text = p["text"]
                if p.get("roll"):
                    r = p["roll"]
                    text += f"\nБросок: {r['die']} {p['check']['bonus']:+d} = {r['total']}."
                if extra:
                    text += "\n" + "\n".join(extra)
                log(state, p["title"], text)
                state["pending"] = None
            elif kind == "scene":
                require(state["status"] == "active", "Возобновите игру.")
                require(
                    not state["pending"] and not state["world"]["combat"],
                    "Сначала завершите текущий шаг и бой.",
                )
                scene = data.get("scene")
                require(scene in cat["scenes"] and scene != state["scene"], "Выберите другую сцену.")
                if state.get("definition"):
                    require(scene in rules.exits(state), "Переход пока закрыт условиями приключения.")
                db.execute(
                    "INSERT INTO checkpoints(session, body) VALUES (?, ?)", (state["id"], encode(state))
                )
                state["scene"] = scene
                for c in state["characters"].values():
                    c["location"] = scene
                log(state, cat["scenes"][scene]["name"], cat["scenes"][scene]["text"])
            elif kind == "player_edit":
                require(state["status"] in {"active", "paused"}, "Игра сейчас не принимает исправления.")
                user = str(data["user_id"])
                participant = state["participants"].get(user)
                require(participant is not None, "Подключитесь заново.")
                p = state["pending"]
                action = next((a for a in state["queue"] if a["id"] == data["action_id"]), None)
                pending = p and p["action"]["id"] == data["action_id"]
                if pending:
                    require(
                        p["phase"] != "waiting" and not p.get("roll"),
                        "Запрос броска уже отправлен. Попросите ведущего отменить шаг.",
                    )
                    action = p["action"]
                require(
                    action is not None and action.get("sender") == user,
                    "Это действие уже завершено или принадлежит другому участнику.",
                )
                require(
                    0 < len(data["text"].strip()) <= 2000 and data["intent"] in cat["intents"],
                    "Проверьте текст действия.",
                )
                action.update(text=data["text"].strip(), intent=data["intent"])
                if pending:
                    state["pending"] = self.prepare(state, action)
            elif kind == "undo":
                if state["pending"]:
                    state["pending"] = None
                    log(
                        state,
                        "Отмена",
                        "Незавершённый шаг отменён. Старый запрос броска недействителен.",
                        "system",
                    )
                else:
                    row = db.execute(
                        "SELECT id, body FROM checkpoints WHERE session=? ORDER BY id DESC LIMIT 1",
                        (state["id"],),
                    ).fetchone()
                    require(row is not None, "Пока нечего отменять.")
                    import json

                    restored = json.loads(row["body"])
                    for key in ("participants", "invite", "gm_invite", "invite_expires", "revision"):
                        restored[key] = state[key]
                    restored.update(queue=[], pending=None)
                    state = restored
                    db.execute("DELETE FROM checkpoints WHERE id=?", (row["id"],))
                    log(
                        state,
                        "Отмена последнего шага",
                        "Состояние восстановлено. Зависимые действия сняты; уже услышанное игроки помнят.",
                        "system",
                    )
            elif kind == "finish":
                require(
                    self.presentation(state)["can_finish"] and not state["pending"],
                    "Сначала достигните финала приключения и завершите шаг.",
                )
                state["status"] = "finished"
                ending = (
                    state["definition"]["endings"][state["world"]["ending"]]["text"]
                    if state.get("definition")
                    else "Вода вернулась в деревню. Спасибо за игру!"
                )
                log(state, "Приключение завершено", ending)
            else:
                raise ValueError("Неизвестная команда")
            if kind not in {"undo", "scene"}:
                self.advance(state)
            self.validate(state)
            self.notify_players(state, kind, db)
            return state

        return self.store.mutate(command_id or uid(), expected, change)

    def pending(self, state, phases):
        require(state["status"] == "active", "Возобновите игру.")
        p = state["pending"]
        require(p is not None and p["phase"] in phases, "Текущий шаг уже изменился.")
        return p

    def prepare(self, state, action):
        try:
            result = self.rules_for(state).prepare(state, action)
        except GameError as exc:
            result = {
                "action": action,
                "phase": "clarify",
                "title": catalog_for(state)["intents"].get(action["intent"], "Действие"),
                "text": str(exc),
                "prompt": "Выберите другой подход или задайте исход вручную.",
                "note": "Проверка правил",
                "check": None,
                "roll": None,
                "effects": [],
                "changes": [],
                "in_combat": False,
            }
        result["id"] = uid()
        return result

    def advance(self, state):
        if state["status"] == "active" and state["pending"] is None and state["queue"]:
            state["pending"] = self.prepare(state, state["queue"].pop(0))

    def join(self, state, data):
        require(state["status"] == "lobby", "Подключение новых участников доступно до начала игры.")
        require(time.time() < state["invite_expires"], "Приглашение истекло. Создайте новую сессию.")
        user = str(data["user_id"])
        require(user not in state["participants"], "Вы уже подключены.")
        token = data.get("token", "")
        if secrets.compare_digest(token, state["gm_invite"]):
            require(
                not any(p["role"] == "gm" for p in state["participants"].values()), "Ведущий уже подключён."
            )
            state["participants"][user] = {"role": "gm", "actor": None}
        else:
            require(secrets.compare_digest(token, state["invite"]), "Неверное приглашение.")
            actor = data.get("actor")
            require(actor in state["characters"], "Выберите доступного персонажа.")
            require(
                not any(p.get("actor") == actor for p in state["participants"].values()),
                "Персонаж уже занят.",
            )
            state["participants"][user] = {"role": "player", "actor": actor}

    def rules_for(self, state):
        return DeclarativeRules(state["definition"]) if state.get("definition") else self.rules

    def validate(self, state):
        self.rules_for(state).validate(state)

    def presentation(self, state):
        result = self.scene_presentation(state)
        rules = self.rules_for(state)
        # Use the same rule validation as execution; the UI never infers prerequisites.
        result["availability"] = {}
        for actor in state["characters"]:
            targets = result["availability"][actor] = {}
            for target in ["", *state["characters"]]:
                options = targets[target] = {}
                for intent in result["quick_actions"]:
                    action = {"actor": actor, "target": target or None, "intent": intent, "text": intent}
                    try:
                        plan = rules.prepare(state, action)
                        options[intent] = plan["text"] if plan["phase"] == "clarify" else ""
                    except GameError as exc:
                        options[intent] = str(exc)
        return result

    def scene_presentation(self, state):
        if state.get("definition"):
            return self.rules_for(state).presentation(state)
        w = state["world"]
        return {
            "quick_actions": (
                ["attack", "promise", "heal", "help", "retreat"]
                if w["combat"]
                else ["inspect", "read", "clean", "install", "start_pump", "heal", "help"]
                if state["scene"] == "reception"
                else ["talk", "promise", "persuade", "pick", "force", "take", "heal", "help"]
            ),
            "exits": [k for k in catalog_for(state)["scenes"] if k != state["scene"]],
            "objectives": [
                {"text": text, "done": bool(done)}
                for text, done in [
                    ("Разобраться с насосом", state["clues"]),
                    ("Найти сердечник", w["core"] != "box"),
                    ("Очистить фильтр", w["filter_clean"]),
                    ("Вернуть подачу воды", w["pump_on"]),
                ]
            ],
            "can_finish": w["pump_on"],
            "ending_title": "Вода снова течёт",
            "guidance": self.waterworks_guidance(state),
        }

    @staticmethod
    def waterworks_guidance(state):
        w = state["world"]
        if w["pump_on"]:
            return "Вода пошла. Нажмите «Завершить приключение»."
        if w["combat"]:
            return "Ход отмеченного персонажа. Можно атаковать, отступить или обещать Аде починить насос — это завершит бой."
        if state["scene"] == "workshop":
            if w["core"] != "box":
                return "Сердечник найден. Перейдите в приёмную для ремонта насоса."
            if w["box_open"]:
                return "Ящик открыт. Выберите персонажа и нажмите «Забрать сердечник»."
            return (
                "Поговорите с Адой. Обещание безопасно починить насос позволит получить сердечник без броска."
            )
        if w["core"] == "box":
            return "За сердечником нужно перейти в мастерскую: нажмите «Сменить локацию» вверху. Фильтр можно очистить сейчас или после возвращения."
        if not w["filter_clean"]:
            return "Очистите фильтр перед запуском насоса."
        if w["core"] != "pump":
            name = state["characters"][w["core"]]["name"]
            return f"Сердечник несёт {name}. Выберите этого персонажа и установите сердечник."
        return "Фильтр чист, сердечник установлен. Можно запустить насос."

    def player_view(self, state, user_id):
        participant = state["participants"].get(str(user_id))
        require(participant and participant["role"] == "player", "Сначала подключитесь по приглашению.")
        actor = participant["actor"]
        p = state["pending"]
        request = None
        if p and p["phase"] == "waiting" and p["action"]["actor"] == actor:
            request = {
                "id": p["request_id"],
                "label": p["check"]["label"],
                "bonus": p["check"]["bonus"],
                "dice": p["check"].get("dice", 20),
            }
        # Explicit allowlist: never serialize the world, pending plan or GM journal here.
        return {
            "character": state["characters"][actor],
            "status": state["status"],
            "clues": [catalog_for(state)["clues"][k] for k in state["clues"]],
            "request": request,
        }

    def notify_players(self, state, kind, db):
        for user, participant in state["participants"].items():
            if participant["role"] != "player":
                continue
            if kind == "request_roll" and state["pending"]["action"]["actor"] == participant["actor"]:
                p = state["pending"]
                self.store.enqueue(
                    {
                        "session": state["id"],
                        "chat_id": user,
                        "request_id": p["request_id"],
                        "text": f"Проверка: {p['check']['label']} ({p['check']['bonus']:+d}).",
                        "reply_markup": {
                            "inline_keyboard": [
                                [
                                    {
                                        "text": f"Бросить d{p['check'].get('dice', 20)}",
                                        "callback_data": "roll:" + p["request_id"],
                                    }
                                ],
                                [{"text": "Настоящий кубик", "callback_data": "physical:" + p["request_id"]}],
                            ]
                        },
                    },
                    db,
                )
            elif kind in {"accept", "undo", "start", "finish"}:
                c = state["characters"][participant["actor"]]
                self.store.enqueue(
                    {
                        "session": state["id"],
                        "chat_id": user,
                        "text": f"{c['name']} · {c['hp']}/{c['max_hp']} HP\n"
                        + "\n".join(catalog_for(state)["clues"][k] for k in state["clues"]),
                    },
                    db,
                )
