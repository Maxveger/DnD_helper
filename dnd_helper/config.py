"""Local settings are independent of source code, releases and game exports."""

import os
import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


def data_directory():
    if os.environ.get("DND_HELPER_DATA_DIR"):
        return Path(os.environ["DND_HELPER_DATA_DIR"])
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "DnDHelper"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "dnd-helper"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    openai_key: str = Field(default="", max_length=512)
    telegram_token: str = Field(default="", max_length=256)
    ai_enabled: bool = False
    telegram_enabled: bool = False
    model: str = Field(default="gpt-5.4-mini", pattern=r"^[a-zA-Z0-9._-]{1,100}$")
    transcription_model: str = "gpt-4o-mini-transcribe"
    budget: float = Field(default=1.0, ge=0.01, le=100)


class Config:
    def __init__(self, directory):
        self.path = Path(directory) / "settings.local.json"
        self.lock = threading.RLock()
        self.settings = Settings()
        if self.path.exists():
            self.settings = Settings.model_validate_json(self.path.read_text("utf-8"))

    def get(self):
        with self.lock:
            return self.settings.model_copy()

    def public(self):
        s = self.get()
        return {
            **s.model_dump(exclude={"openai_key", "telegram_token"}),
            "has_openai_key": bool(s.openai_key),
            "has_telegram_token": bool(s.telegram_token),
        }

    def save(self, values):
        with self.lock:
            current = self.settings.model_dump()
            # Empty password fields keep the existing key; explicit clear flags remove it.
            for key in ("openai_key", "telegram_token"):
                if values.pop("clear_" + key, False):
                    current[key] = ""
                if not values.get(key):
                    values.pop(key, None)
            current.update(values)
            result = Settings.model_validate(current)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                out.write(result.model_dump_json(indent=2))
            os.replace(temp, self.path)
            self.settings = result
            return self.public()
