"""Coordinates optional integrations without holding database locks during network calls."""

import threading
from copy import deepcopy

from .config import Config
from .adventures import AdventureLibrary, catalog_for
from .engine import Engine, uid
from .providers import OpenAIProvider, PRICES, demo_intent
from .rules import GameError
from .storage import Store
from .telegram import TelegramBot


class Service:
    def __init__(self, directory):
        self.config = Config(directory)
        self.store = Store(directory / "game.sqlite3")
        self.adventures = AdventureLibrary(self.store)
        self.engine = Engine(self.store, adventures=self.adventures)
        self.provider = OpenAIProvider(self.config, self.store)
        self.bot = TelegramBot(self)
        self.stop_event = threading.Event()
        self.worker = None
        self.ai_status = "Готовые подсказки"
        self.attempted = set()

    def start(self):
        self.bot.start()
        self.worker = threading.Thread(target=self.run_ai, daemon=True, name="ai-planner")
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        self.bot.stop()
        if self.worker:
            self.worker.join(timeout=27)

    def submit(
        self, actor, text, intent=None, target=None, source="web", sender=None, command_id=None, expected=None
    ):
        # Explicit action buttons always stay deterministic, including when GPT is enabled.
        settings = self.config.get()
        if intent is None:
            intent = (
                "unknown"
                if settings.ai_enabled and settings.openai_key
                else demo_intent(text, self.store.read())
            )
        return self.engine.command(
            "submit",
            {
                "actor": actor,
                "text": text,
                "intent": intent,
                "target": target,
                "source": source,
                "sender": sender,
            },
            command_id,
            expected,
        )

    def run_ai(self):
        while not self.stop_event.wait(0.5):
            state = self.store.read()
            settings = self.config.get()
            p = state["pending"] if state else None
            if not (
                settings.ai_enabled
                and settings.openai_key
                and state
                and state["status"] == "active"
                and p
                and p["phase"] == "clarify"
                and p["action"]["intent"] == "unknown"
                and not p.get("ai_done")
                and p["id"] not in self.attempted
            ):
                continue
            self.attempted.add(p["id"])
            self.ai_status = "Разбираю действие…"
            try:
                plan = self.provider.plan(state, p["action"])
                self.engine.command(
                    "model_plan", {**plan.model_dump(), "pending_id": p["id"]}, command_id="ai:" + p["id"]
                )
                self.ai_status = "Действие проверено правилами"
            except GameError as exc:
                self.ai_status = str(exc)
            except Exception:
                self.ai_status = "Не удалось обработать действие. Доступен ручной исход."

    def rewrite(self, expected):
        state = self.store.read()
        if (
            not state
            or not state["pending"]
            or state["pending"]["phase"] != "ready"
            or state["revision"] != expected
        ):
            raise GameError("Сначала дождитесь готового результата.")
        p = state["pending"]
        result = self.provider.narrate(state, p)
        return self.engine.command("narrate", {**result.model_dump(), "pending_id": p["id"]}, uid(), expected)

    def view(self):
        state = self.store.read()
        settings = self.config.public()
        return {
            "game": state,
            "settings": settings,
            "telegram": {"status": self.bot.status, "username": self.bot.username},
            "ai_status": self.ai_status,
            "spending": self.store.spending(state["id"])
            if state
            else {"total": 0, "calls": 0, "pending": 0, "recent": []},
            "catalog": {**catalog_for(state), "models": list(PRICES)},
            "presentation": self.engine.presentation(state) if state else None,
            "adventures": [{"key": "builtin", "version": "0.1", **catalog_for()}] + self.adventures.list(),
        }

    def export(self):
        state = self.store.read()
        if not state:
            raise GameError("Нет игры для экспорта.")
        state = deepcopy(state)
        for key in ("participants", "invite", "gm_invite", "invite_expires"):
            state.pop(key, None)
        for action in state["queue"]:
            action.pop("sender", None)
        if state["pending"]:
            state["pending"]["action"].pop("sender", None)
        return {"format": "dnd-helper-save@1", "game": state}
