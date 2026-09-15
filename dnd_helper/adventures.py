"""Versioned adventure documents: strict data, local validation, immutable library.

No eval, code loading, external references or model calls. The accepted document
is pinned inside each game; importing a revision never rewrites a running game.
"""

import hashlib
import json
import re
from copy import deepcopy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import content
from .rules import require

MAX_DOCUMENT_BYTES = 500_000
Id = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")]
Text = Annotated[str, Field(min_length=1, max_length=3000)]
Name = Annotated[str, Field(min_length=1, max_length=120)]
Scalar = bool | int | str


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Variable(Model):
    initial: Scalar
    minimum: int = Field(default=-1000, ge=-100000, le=100000)
    maximum: int = Field(default=1000, ge=-100000, le=100000)
    choices: list[Name] = Field(default_factory=list, max_length=30)


class Condition(Model):
    kind: Literal["variable", "clue", "item", "hp"]
    ref: Id
    op: Literal["eq", "ne", "gte", "lte"] = "eq"
    value: Scalar
    who: Literal["actor", "target"] = "actor"


class SetEffect(Model):
    op: Literal["set"]
    ref: Id
    value: Scalar


class IncrementEffect(Model):
    op: Literal["increment"]
    ref: Id
    value: int = Field(ge=-1000, le=1000)


class RevealEffect(Model):
    op: Literal["reveal"]
    ref: Id


class ItemEffect(Model):
    op: Literal["give", "take"]
    ref: Id
    who: Literal["actor", "target"] = "actor"


class HpEffect(Model):
    op: Literal["hp"]
    value: int = Field(ge=-1000, le=1000)
    who: Literal["actor", "target"] = "actor"


class MoveEffect(Model):
    op: Literal["move"]
    ref: Id


class EndEffect(Model):
    op: Literal["end"]
    ref: Id


Effect = Annotated[
    SetEffect | IncrementEffect | RevealEffect | ItemEffect | HpEffect | MoveEffect | EndEffect,
    Field(discriminator="op"),
]


class Outcome(Model):
    text: Text
    prompt: Name = "Что делаете дальше?"
    effects: list[Effect] = Field(default_factory=list, max_length=30)


class Check(Model):
    stat: Id
    dc: int = Field(ge=-100, le=200)
    bonus: int = Field(default=0, ge=-50, le=50)


class Action(Model):
    name: Name
    description: Text
    scenes: list[Id] = Field(min_length=1, max_length=30)
    requires: list[Condition] = Field(default_factory=list, max_length=30)
    blocked: Text = "Сейчас это недоступно. Попробуйте другой подход."
    once: bool = False
    target: Literal["none", "self", "ally", "any"] = "none"
    check: Check | None = None
    success: Outcome
    failure: Outcome | None = None


class Exit(Model):
    to: Id
    when: list[Condition] = Field(default_factory=list, max_length=20)


class Scene(Model):
    name: Name
    text: Text
    prompt: Name
    secret: Text
    hint: Text
    exits: list[Exit] = Field(default_factory=list, max_length=30)


class Character(Model):
    name: Name
    role: Name
    description: Text
    max_hp: int = Field(ge=1, le=1000)
    stats: dict[Id, Annotated[int, Field(ge=-50, le=50)]] = Field(min_length=1, max_length=12)
    items: list[Id] = Field(default_factory=list, max_length=40)


class NPC(Model):
    name: Name
    public: Text
    secret: Text
    scenes: list[Id] = Field(min_length=1, max_length=30)


class Objective(Model):
    text: Name
    when: list[Condition] = Field(min_length=1, max_length=20)


class Ending(Model):
    name: Name
    text: Text


class RulesDefinition(Model):
    engine: Literal["declarative@1"]
    name: Name
    summary: Text
    dice: Literal[4, 6, 8, 10, 12, 20, 100] = 20
    attributes: dict[Id, Name] = Field(min_length=1, max_length=12)
    manual_rules: list[Text] = Field(default_factory=list, max_length=20)


class Adventure(Model):
    format: Literal["dnd-adventure@1"]
    id: Id
    version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$", max_length=30)]
    title: Name
    summary: Text
    lore: Text
    gm_notes: Text
    duration_minutes: int = Field(ge=10, le=600)
    min_players: int = Field(ge=1, le=6)
    max_players: int = Field(ge=1, le=6)
    rules: RulesDefinition
    start: Id
    characters: dict[Id, Character] = Field(min_length=1, max_length=12)
    scenes: dict[Id, Scene] = Field(min_length=1, max_length=30)
    npcs: dict[Id, NPC] = Field(default_factory=dict, max_length=30)
    items: dict[Id, Name] = Field(default_factory=dict, max_length=80)
    clues: dict[Id, Text] = Field(default_factory=dict, max_length=80)
    variables: dict[Id, Variable] = Field(default_factory=dict, max_length=80)
    actions: dict[Id, Action] = Field(min_length=1, max_length=120)
    objectives: list[Objective] = Field(default_factory=list, max_length=20)
    endings: dict[Id, Ending] = Field(min_length=1, max_length=20)


def valid_value(definition, value):
    initial = definition["initial"]
    if type(value) is not type(initial):
        return False
    if type(value) is int:
        return definition["minimum"] <= value <= definition["maximum"]
    if type(value) is str:
        return value in definition["choices"]
    return True


def semantic_issues(d):
    errors, warnings = [], []

    def error(path, message):
        errors.append({"path": path, "message": message})

    def reference(path, ref, collection):
        if ref not in collection:
            error(path, f"Нет объекта «{ref}». Исправьте ID или добавьте его определение.")
            return False
        return True

    def conditions(values, path, target=False, global_scope=False):
        for i, c in enumerate(values):
            p = f"{path}[{i}]"
            kind, value = c["kind"], c["value"]
            if c["who"] == "target" and not target:
                error(p + ".who", "Для target действие должно разрешать выбор цели.")
            if global_scope and kind in {"item", "hp"}:
                error(p, "У цели/перехода нет действующего героя. Используйте variable или clue.")
            if kind == "variable":
                if reference(p + ".ref", c["ref"], d["variables"]):
                    v = d["variables"][c["ref"]]
                    if not valid_value(v, value):
                        error(p + ".value", "Значение должно соответствовать типу и диапазону переменной.")
            elif kind in {"clue", "item"}:
                reference(p + ".ref", c["ref"], d["clues" if kind == "clue" else "items"])
                if type(value) is not bool:
                    error(p + ".value", "Для наличия улики/предмета нужно true или false.")
            elif kind == "hp":
                if c["ref"] != "hp" or type(value) is not int:
                    error(p, 'Для здоровья нужны ref: "hp" и целое value.')
            if c["op"] in {"gte", "lte"} and type(value) is not int:
                error(p + ".op", "gte/lte сравнивают только целые числа. Используйте eq/ne.")

    reference("start", d["start"], d["scenes"])
    if not d["min_players"] <= d["max_players"] <= len(d["characters"]):
        error("max_players", "Нужно min_players ≤ max_players ≤ количество готовых персонажей.")
    if "unknown" in d["actions"]:
        error("actions.unknown", "ID unknown зарезервирован для решения ведущего. Переименуйте действие.")
    if len(set(d["items"].values())) != len(d["items"]):
        error("items", "Названия предметов должны отличаться: карточка хранит их названия.")
    for key, v in d["variables"].items():
        if not valid_value(v, v["initial"]):
            error(f"variables.{key}", "Начальное значение должно входить в minimum/maximum или choices.")
    for key, c in d["characters"].items():
        if set(c["stats"]) != set(d["rules"]["attributes"]):
            error(f"characters.{key}.stats", "Укажите ровно все характеристики из rules.attributes.")
        if len(set(c["items"])) != len(c["items"]):
            error(f"characters.{key}.items", "Уберите повторяющиеся предметы.")
        for item in c["items"]:
            reference(f"characters.{key}.items", item, d["items"])
    graph = {key: set() for key in d["scenes"]}
    for key, s in d["scenes"].items():
        for i, e in enumerate(s["exits"]):
            reference(f"scenes.{key}.exits[{i}].to", e["to"], d["scenes"])
            graph[key].add(e["to"])
            conditions(e["when"], f"scenes.{key}.exits[{i}].when", global_scope=True)
    for key, npc in d["npcs"].items():
        for scene in npc["scenes"]:
            reference(f"npcs.{key}.scenes", scene, d["scenes"])
    ending_sources = set()
    for key, a in d["actions"].items():
        path = f"actions.{key}"
        for scene in a["scenes"]:
            reference(path + ".scenes", scene, d["scenes"])
        conditions(a["requires"], path + ".requires", a["target"] != "none")
        if bool(a["check"]) != bool(a["failure"]):
            error(path, "Проверка check и исход failure нужны вместе; без броска оставьте оба null.")
        if a["check"]:
            reference(path + ".check.stat", a["check"]["stat"], d["rules"]["attributes"])
        for branch in ("success", "failure"):
            outcome = a[branch]
            if not outcome:
                continue
            for i, e in enumerate(outcome["effects"]):
                p, op = f"{path}.{branch}.effects[{i}]", e["op"]
                if e.get("who") == "target" and a["target"] == "none":
                    error(p + ".who", "У действия нет цели. Задайте target: self, ally или any.")
                collections = {
                    "set": "variables",
                    "increment": "variables",
                    "reveal": "clues",
                    "give": "items",
                    "take": "items",
                    "move": "scenes",
                    "end": "endings",
                }
                if op in collections and reference(p + ".ref", e["ref"], d[collections[op]]):
                    if op == "set" and not valid_value(d["variables"][e["ref"]], e["value"]):
                        error(p + ".value", "Значение не соответствует определению переменной.")
                    if op == "increment" and type(d["variables"][e["ref"]]["initial"]) is not int:
                        error(p, "increment поддерживает только целочисленные переменные.")
                if op == "move":
                    for scene in a["scenes"]:
                        if scene in graph:
                            graph[scene].add(e["ref"])
                if op == "end":
                    ending_sources.update(a["scenes"])
                    if i != len(outcome["effects"]) - 1:
                        error(p, "end должен быть последним эффектом исхода.")
    for i, objective in enumerate(d["objectives"]):
        conditions(objective["when"], f"objectives[{i}].when", global_scope=True)
    reached, todo = set(), [d["start"]]
    while todo:
        scene = todo.pop()
        if scene not in reached:
            reached.add(scene)
            todo.extend(graph.get(scene, set()) - reached)
    if not ending_sources.intersection(reached):
        error("endings", "Нет структурного пути от стартовой сцены к действию с эффектом end.")
    for scene in set(d["scenes"]) - reached:
        warnings.append({"path": f"scenes.{scene}", "message": "До сцены нет перехода от старта."})
    for i, rule in enumerate(d["rules"]["manual_rules"]):
        warnings.append({"path": f"rules.manual_rules[{i}]", "message": "Только решение ведущего: " + rule})
    warnings.append(
        {
            "path": "actions",
            "message": "Ссылки и типы проверены. Логическую проходимость всех веток, баланс и совпадение текста с эффектами нужно проверить пробной игрой.",
        }
    )
    return errors, warnings


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Повторяющееся поле «{key}». Оставьте одно значение.")
        result[key] = value
    return result


def validate_document(text):
    errors, warnings, document = [], [], None
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        errors = [{"path": "$", "message": "Нужен текст JSON до 500 КБ. Сократите приключение."}]
    else:
        text = text.lstrip("\ufeff").strip()
        # Accept a single Markdown fence, never guess which object in a long chat was intended.
        fence = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", text)
        if fence:
            text = fence[1]
        try:
            raw = json.loads(
                text,
                object_pairs_hook=_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NaN/Infinity недопустимы.")),
            )
            document = Adventure.model_validate(raw).model_dump()
            errors, warnings = semantic_issues(document)
        except json.JSONDecodeError as exc:
            errors = [
                {
                    "path": f"строка {exc.lineno}, столбец {exc.colno}",
                    "message": "Некорректный JSON. Проверьте кавычки, запятые и полноту документа. Нужен один объект без пояснений.",
                }
            ]
        except ValidationError as exc:
            translations = {
                "missing": "Обязательное поле отсутствует.",
                "extra_forbidden": "Поле не поддерживается. Уберите его или перенесите пояснение в gm_notes/manual_rules.",
                "literal_error": "Неподдерживаемая версия или значение. Используйте значения из схемы.",
                "int_type": "Нужно целое число, без кавычек.",
                "bool_type": "Нужно true или false, без кавычек.",
                "string_type": "Нужна строка текста.",
            }
            errors = [
                {
                    "path": ".".join(map(str, e["loc"])) or "$",
                    "message": translations.get(e["type"], "Неверный тип, диапазон или формат. " + e["msg"]),
                }
                for e in exc.errors(include_input=False, include_url=False)[:60]
            ]
        except (ValueError, RecursionError) as exc:
            errors = [
                {
                    "path": "$",
                    "message": str(exc)[:200]
                    if not isinstance(exc, RecursionError)
                    else "Слишком глубокая вложенность JSON.",
                }
            ]
    ok = not errors
    report = {"ok": ok, "errors": errors, "warnings": warnings, "preview": None, "repair_prompt": ""}
    if ok:
        report["preview"] = {
            "title": document["title"],
            "summary": document["summary"],
            "version": document["version"],
            "scenes": len(document["scenes"]),
            "actions": len(document["actions"]),
            "rules": document["rules"]["name"],
            "players": f"{document['min_players']}–{document['max_players']}",
            "manual_rules": document["rules"]["manual_rules"],
        }
    else:
        report["repair_prompt"] = (
            "Исправь последний документ приключения dnd-adventure@1 по отчёту приложения. Сохрани согласованные лор и замысел. Не скрывай неподдерживаемые механики: обсуди адаптацию или явно внеси их в rules.manual_rules. Верни полный исправленный JSON, без сокращений и комментариев.\n\n"
            + "\n".join(f"- {e['path']}: {e['message']}" for e in errors)
        )
    return report, document if ok else None


class AdventureLibrary:
    def __init__(self, store):
        self.store = store

    def install(self, text):
        report, document = validate_document(text)
        if document:
            digest = hashlib.sha256(
                json.dumps(document, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            self.store.set_meta("adventure:" + digest, document)
            report["key"] = digest
        return report

    def get(self, key):
        require(isinstance(key, str) and re.fullmatch(r"[a-f0-9]{64}", key), "Неверный ID приключения.")
        result = self.store.get_meta("adventure:" + key)
        require(result is not None, "Приключение не найдено. Загрузите документ снова.")
        return result

    def list(self):
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT key, value FROM meta WHERE key LIKE 'adventure:%' ORDER BY rowid DESC"
            ).fetchall()
        return [{"key": r["key"][10:], **library_entry(json.loads(r["value"]))} for r in rows]


def library_entry(d):
    return {
        "title": d["title"],
        "version": d["version"],
        "summary": d["summary"],
        "characters": character_catalog(d),
        "min_players": d["min_players"],
        "max_players": d["max_players"],
    }


def character_catalog(d):
    return {
        key: {**deepcopy(c), "id": key, "initial": c["name"][0], "items": [d["items"][i] for i in c["items"]]}
        for key, c in d["characters"].items()
    }


def catalog_for(state=None):
    d = state.get("definition") if state else None
    if not d:
        return {
            "characters": content.CHARACTERS,
            "scenes": content.SCENES,
            "intents": content.INTENTS,
            "attributes": content.ATTRIBUTES,
            "clues": content.CLUES,
            "adventure": content.ADVENTURE,
            "title": "Сердце водокачки",
            "summary": "В деревне пропала вода. Найдите причину остановки старой водокачки.",
            "min_players": 1,
            "max_players": 2,
            "dice": 20,
            "rules_summary": None,
        }
    return {
        "characters": character_catalog(d),
        "scenes": {
            k: {**s, "eyebrow": f"{i + 1:02} / {s['name']}"} for i, (k, s) in enumerate(d["scenes"].items())
        },
        "intents": {**{k: a["name"] for k, a in d["actions"].items()}, "unknown": "Решение ведущего"},
        "attributes": d["rules"]["attributes"],
        "clues": d["clues"],
        "adventure": d["id"] + "@" + d["version"],
        "title": d["title"],
        "summary": d["summary"],
        "min_players": d["min_players"],
        "max_players": d["max_players"],
        "dice": d["rules"]["dice"],
        "rules_summary": d["rules"]["summary"],
        "manual_rules": d["rules"]["manual_rules"],
        "lore": d["lore"],
        "gm_notes": d["gm_notes"],
        "npcs": d["npcs"],
    }


def author_kit():
    from pathlib import Path

    resources = Path(__file__).parent / "resources"
    schema = Adventure.model_json_schema()
    example = json.loads((resources / "example-adventure.json").read_text("utf-8"))
    prompt = (resources / "author-prompt.md").read_text("utf-8")
    prompt += "\nJSON SCHEMA\n```json\n" + json.dumps(schema, ensure_ascii=False, indent=2) + "\n```\n"
    prompt += "\nРАБОЧИЙ ПРИМЕР\n```json\n" + json.dumps(example, ensure_ascii=False, indent=2) + "\n```\n"
    return {"prompt": prompt, "schema": schema, "example": example}
