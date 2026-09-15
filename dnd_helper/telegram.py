"""Telegram is an adapter: identity, delivery and voice; game decisions stay in Engine."""

import hashlib
import secrets
import threading

import httpx

from .adventures import catalog_for
from .rules import GameError, require


class TelegramBot:
    def __init__(self, service, transport=None):
        self.service = service
        self.transport = transport
        self.stop_event = threading.Event()
        self.thread = None
        self.status = "Не подключён"
        self.username = ""
        self.token = ""

    def start(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name="telegram")
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=22)

    def call(self, method, **payload):
        with httpx.Client(timeout=20, transport=self.transport, trust_env=False) as client:
            response = client.post(f"https://api.telegram.org/bot{self.token}/{method}", json=payload)
            response.raise_for_status()
            body = response.json()
            if not body.get("ok"):
                raise GameError("Telegram отклонил запрос. Проверьте токен и доступ бота.")
            return body["result"]

    def send(self, chat_id, text, markup=None):
        self.service.store.enqueue(
            {"chat_id": str(chat_id), "text": text, **({"reply_markup": markup} if markup else {})}
        )

    def flush(self):
        state = self.service.store.read()
        for key, item in self.service.store.outbox():
            request = item.pop("request_id", None)
            session = item.pop("session", None)
            if session and (not state or state["id"] != session):
                self.service.store.delivered(key)
                continue
            if request and (
                not state
                or not state["pending"]
                or state["pending"].get("request_id") != request
                or state["pending"]["phase"] != "waiting"
            ):
                self.service.store.delivered(key)
                continue
            try:
                self.call("sendMessage", **item)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {400, 403}:
                    raise
                # One deleted chat or blocked bot must not hold up everyone's delivery.
                self.service.store.set_meta(
                    "telegram_delivery_notice", "Одно сообщение не доставлено: чат недоступен."
                )
            self.service.store.delivered(key)

    def run(self):
        while not self.stop_event.is_set():
            settings = self.service.config.get()
            if not settings.telegram_enabled or not settings.telegram_token:
                self.status = "Не подключён"
                self.username = ""
                self.stop_event.wait(1)
                continue
            try:
                if self.token != settings.telegram_token or not self.username:
                    self.token = settings.telegram_token
                    self.username = ""
                    me = self.call("getMe")
                    self.username = me["username"]
                    # Never silently delete an existing webhook belonging to another deployment.
                    info = self.call("getWebhookInfo")
                    if info.get("url"):
                        self.username = ""
                        raise GameError(
                            "У бота настроен webhook. Используйте отдельного тестового бота или отключите webhook."
                        )
                self.status = "Подключён"
                self.flush()
                offset_key = "tg_offset_" + hashlib.sha256(self.token.encode()).hexdigest()[:16]
                offset = self.service.store.get_meta(offset_key, 0)
                updates = self.call(
                    "getUpdates", offset=offset, timeout=2, allowed_updates=["message", "callback_query"]
                )
                for update in updates:
                    self.process_update(update)
                    self.service.store.set_meta(offset_key, update["update_id"] + 1)
                self.flush()
            except GameError as exc:
                self.status = str(exc)
                self.stop_event.wait(3)
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                self.status = (
                    "Нет связи с Telegram. Проверьте интернет, токен и второй запущенный экземпляр бота."
                )
                self.stop_event.wait(3)

    def process_update(self, update):
        msg = update.get("message")
        callback = update.get("callback_query")
        if callback:
            msg = callback.get("message")
        if not msg or msg.get("chat", {}).get("type") != "private":
            return
        user = str((callback or msg)["from"]["id"])
        chat_id = msg["chat"]["id"]
        key = f"tg:{hashlib.sha256(self.token.encode()).hexdigest()[:12]}:{update['update_id']}"
        if self.service.store.get_meta(key + ":done", False):
            return
        try:
            if callback:
                self.call("answerCallbackQuery", callback_query_id=callback["id"])
                self.callback(user, chat_id, callback.get("data", ""), key)
            else:
                self.message(user, chat_id, msg, key)
        except GameError as exc:
            self.send(chat_id, str(exc))
        self.service.store.set_meta(key + ":done", True)

    def participant(self, user):
        state = self.service.store.read()
        require(state is not None, "Ведущий ещё не создал игру.")
        p = state["participants"].get(user)
        require(p is not None, "Подключитесь по ссылке приглашения от ведущего.")
        return state, p

    def callback(self, user, chat_id, data, key):
        if data.startswith("join:"):
            token = self.service.store.get_meta("invite:" + user, "")
            self.service.engine.command("join", {"token": token, "actor": data[5:], "user_id": user}, key)
            self.card(user, chat_id)
            return
        state, p = self.participant(user)
        if data.startswith("correct:"):
            self.service.store.set_meta("correct:" + user, data[8:])
            self.send(
                chat_id,
                "Отправьте исправленное действие текстом. Исправление доступно до запроса броска или принятия исхода.",
            )
        elif data.startswith(("roll:", "physical:")):
            require(p["role"] == "player", "Этот запрос предназначен игроку.")
            request = self.service.engine.player_view(state, user)["request"]
            wanted = data.split(":", 1)[1]
            require(request and request["id"] == wanted, "Бросок уже сделан или запрос отменён.")
            if data.startswith("physical:"):
                self.service.store.set_meta("physical:" + user, wanted)
                self.send(
                    chat_id,
                    f"Бросьте настоящий d{request['dice']} и отправьте число от 1 до {request['dice']}. Бонус добавим сами.",
                )
            else:
                result = self.service.engine.command("roll", {"request_id": wanted, "user_id": user}, key)
                roll = result["pending"]["roll"]
                self.send(chat_id, f"Выпало {roll['die']}; итог с бонусом: {roll['total']}. Ждём ведущего.")
        elif data.startswith("actor:"):
            require(p["role"] == "gm" and data[6:] in state["characters"], "Недоступно.")
            self.service.store.set_meta("gm_actor:" + user, data[6:])
            self.send(chat_id, "Персонаж для следующих действий выбран.")
        elif data.startswith("mode:"):
            require(p["role"] == "gm" and data[5:] in {"action", "outcome"}, "Недоступно.")
            self.service.store.set_meta("gm_mode:" + user, data[5:])
            self.send(
                chat_id,
                "Отправьте действие группы."
                if data[5:] == "action"
                else "Опишите свой исход. Он появится на экране ведущего для принятия; HP правятся на ноутбуке.",
            )
        else:
            self.card(user, chat_id)

    def message(self, user, chat_id, msg, key):
        text = msg.get("text", "").strip()
        if text.startswith("/start"):
            state = self.service.store.read()
            require(state is not None, "Ведущий ещё не создал игру.")
            if user in state["participants"]:
                self.card(user, chat_id)
                return
            parts = text.split(maxsplit=1)
            require(len(parts) == 2, "Откройте ссылку приглашения от ведущего.")
            token = parts[1]
            if secrets.compare_digest(token, state["gm_invite"]):
                self.service.engine.command("join", {"token": token, "user_id": user}, key)
                self.card(user, chat_id)
                return
            require(secrets.compare_digest(token, state["invite"]), "Приглашение недействительно.")
            self.service.store.set_meta("invite:" + user, token)
            taken = {p.get("actor") for p in state["participants"].values()}
            buttons = [
                [{"text": c["name"] + " · " + c["role"], "callback_data": "join:" + cid}]
                for cid, c in state["characters"].items()
                if cid not in taken
            ]
            self.send(chat_id, "Выберите персонажа:", {"inline_keyboard": buttons})
            return
        state, p = self.participant(user)
        if text.lower() in {
            "/character",
            "/help",
            "персонаж",
            "инвентарь",
            "что я умею",
            "открытия",
            "/menu",
        }:
            self.card(user, chat_id)
            return
        correction = self.service.store.get_meta("correct:" + user)
        if correction and text:
            from .providers import demo_intent

            config = self.service.config.get()
            intent = "unknown" if config.ai_enabled and config.openai_key else demo_intent(text, state)
            try:
                self.service.engine.command(
                    "player_edit",
                    {"user_id": user, "action_id": correction, "text": text, "intent": intent},
                    key,
                )
            finally:
                self.service.store.set_meta("correct:" + user, None)
            self.send(chat_id, "Действие исправлено.")
            return
        physical = self.service.store.get_meta("physical:" + user)
        if p["role"] == "player" and physical and text.isdigit():
            request = self.service.engine.player_view(state, user)["request"]
            require(request and request["id"] == physical, "Этот бросок больше не ожидается.")
            self.service.engine.command(
                "roll", {"request_id": physical, "die": int(text), "user_id": user}, key
            )
            self.service.store.set_meta("physical:" + user, None)
            self.send(chat_id, "Результат передан ведущему.")
            return
        if msg.get("voice"):
            voice = msg["voice"]
            require(
                0 < voice.get("duration", 0) <= 60 and voice.get("file_size", 0) <= 8_000_000,
                "Отправьте запись до 60 секунд и 8 МБ.",
            )
            text = self.service.store.get_meta(key + ":transcript")
            if text is None:
                info = self.call("getFile", file_id=voice["file_id"])
                with httpx.Client(timeout=20, transport=self.transport, trust_env=False) as client:
                    with client.stream(
                        "GET", f"https://api.telegram.org/file/bot{self.token}/{info['file_path']}"
                    ) as response:
                        response.raise_for_status()
                        chunks, size = [], 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            require(size <= 8_000_000, "Запись слишком большая.")
                            chunks.append(chunk)
                text = self.service.provider.transcribe(state["id"], b"".join(chunks), voice["duration"])
                self.service.store.set_meta(key + ":transcript", text)
            self.send(chat_id, "Распознано: " + text)
        require(bool(text), "Отправьте текст или голосовое сообщение.")
        actor = (
            p["actor"]
            if p["role"] == "player"
            else self.service.store.get_meta("gm_actor:" + user, next(iter(state["characters"])))
        )
        if p["role"] == "gm" and self.service.store.get_meta("gm_mode:" + user, "action") == "outcome":
            self.service.engine.command("manual", {"text": text, "actor": actor, "user_id": user}, key)
        else:
            result = self.service.submit(actor, text, source="telegram", sender=user, command_id=key)
            actions = result["queue"] + ([result["pending"]["action"]] if result["pending"] else [])
            action = next(
                (a for a in reversed(actions) if a.get("sender") == user and a["text"] == text), None
            )
            if action:
                self.send(
                    chat_id,
                    "Действие передано ведущему.",
                    {
                        "inline_keyboard": [
                            [{"text": "Исправить текст", "callback_data": "correct:" + action["id"]}]
                        ]
                    },
                )
                return
        self.send(chat_id, "Передано ведущему. Можно продолжать обсуждение за столом.")

    def card(self, user, chat_id):
        state, participant = self.participant(user)
        if participant["role"] == "gm":
            buttons = [
                [
                    {"text": "Действие группы", "callback_data": "mode:action"},
                    {"text": "Мой исход", "callback_data": "mode:outcome"},
                ],
                [
                    {"text": c["name"], "callback_data": "actor:" + cid}
                    for cid, c in state["characters"].items()
                ],
            ]
            self.send(
                chat_id,
                "Вы — ведущий. Выберите режим и персонажа, затем отправьте текст или голосовое.",
                {"inline_keyboard": buttons},
            )
            return
        view = self.service.engine.player_view(state, user)
        c = view["character"]
        text = f"{c['name']} · {c['role']}\nЗдоровье: {c['hp']}/{c['max_hp']}\n\n"
        text += " · ".join(f"{catalog_for(state)['attributes'][k]} {v:+d}" for k, v in c["stats"].items())
        text += "\n\nСнаряжение: " + ", ".join(c["items"])
        text += "\n\nМожно исследовать, разговаривать, помогать, атаковать и использовать вещи. Опишите свой подход."
        if view["clues"]:
            text += "\n\nОткрытия:\n" + "\n".join(view["clues"])
        self.send(
            chat_id,
            text,
            {"keyboard": [["Персонаж", "Инвентарь"], ["Что я умею", "Открытия"]], "resize_keyboard": True},
        )
