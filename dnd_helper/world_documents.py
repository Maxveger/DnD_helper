"""Local author documents and replayable GM journals. No model, code or network on import."""

import json
import re
from pathlib import Path
from copy import deepcopy
from typing import Annotated, Literal

from pydantic import Field, ValidationError

from .adventures import _pairs
from .free_world import Check, Operation, Strict, apply_operations, initial_world
from .rules import GameError, require

MAX_BYTES = 500_000
Id = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,49}$")]
Text = Annotated[str, Field(min_length=1, max_length=3000)]


class Rules(Strict):
    engine: Literal["hybrid-simple@1"] = "hybrid-simple@1"
    manual_rules: list[Text] = Field(default_factory=list, max_length=10)


class WorldEntity(Strict):
    id: Id
    kind: Literal["hero", "location", "npc", "item"]
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(max_length=500)
    location: Id | None
    goal: str = Field(default="", max_length=300)
    hp: int | None = Field(default=None, ge=1, le=100)
    max_hp: int | None = Field(default=None, ge=1, le=100)
    stats: dict[Literal["strength", "agility", "mind"], Annotated[int, Field(ge=-5, le=5)]] | None = None


class WorldFact(Strict):
    id: Id
    text: str = Field(min_length=1, max_length=600)
    status: Literal["fact", "claim", "rumor", "promise"]
    visibility: Literal["public", "gm"]
    known_by: list[Id] = Field(max_length=10)
    resolved: bool = False


class WorldDocument(Strict):
    format: Literal["dnd-world@1"]
    id: Id
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$", max_length=30)
    title: str = Field(min_length=1, max_length=120)
    lore: Text
    intro: Text
    gm_notes: Text
    boundaries: list[Text] = Field(max_length=12)
    rules: Rules
    entities: list[WorldEntity] = Field(min_length=2, max_length=60)
    facts: list[WorldFact] = Field(max_length=200)
    connections: list[list[Id]] = Field(max_length=100)


class JournalAction(Strict):
    id: str = Field(min_length=1, max_length=100)
    actor: Id
    text: str = Field(min_length=1, max_length=2000)
    mode: Literal["action", "hint", "manual"] = "action"


class Roll(Strict):
    value: int = Field(ge=1, le=20)
    bonus: int = Field(ge=-5, le=5)
    dc: Literal[10, 13, 16]
    success: bool


class JournalEvent(Strict):
    id: str = Field(min_length=1, max_length=100)
    action: JournalAction
    summary: str = Field(min_length=1, max_length=800)
    text: str = Field(max_length=1800)
    operations: list[Operation] = Field(max_length=12)
    roll: Roll | None = None
    check: Check | None = None
    gm_hint: str = Field(default="", max_length=800)
    edited: bool = False


class Journal(Strict):
    format: Literal["dnd-world-log@1"]
    document: WorldDocument
    events: list[JournalEvent] = Field(max_length=200)


def validate_world(document):
    entities = {e["id"]: e for e in document["entities"]}
    require(len(entities) == len(document["entities"]), "entities: повторяющиеся ID.")
    require(
        len({e["name"].casefold() for e in entities.values()}) == len(entities),
        "entities: имена должны различаться.",
    )
    heroes = [e for e in entities.values() if e["kind"] == "hero"]
    require(1 <= len(heroes) <= 4, "entities: нужны 1–4 готовых героя.")
    for e in entities.values():
        path = "entities." + e["id"]
        if e["kind"] == "location":
            require(e["location"] is None, path + ".location: для места нужно null.")
        else:
            parent = entities.get(e["location"])
            allowed = {"location", "hero", "npc"} if e["kind"] == "item" else {"location"}
            require(
                parent and parent["kind"] in allowed,
                path + ".location: неизвестное или неподходящее место/владелец.",
            )
        if e["kind"] == "hero":
            require(e["hp"] and e["max_hp"] and e["hp"] <= e["max_hp"], path + ": нужны hp ≤ max_hp.")
            require(
                e["stats"] is not None and set(e["stats"]) == {"strength", "agility", "mind"},
                path + ".stats: нужны strength, agility, mind.",
            )
        else:
            require(
                all(e[k] is None for k in ("hp", "max_hp", "stats")),
                path + ": характеристики поддержаны только для героев.",
            )
    seen = set()
    for edge in document["connections"]:
        require(
            len(edge) == 2
            and edge[0] != edge[1]
            and all(i in entities and entities[i]["kind"] == "location" for i in edge),
            "connections: нужны пары разных существующих мест.",
        )
        require(tuple(sorted(edge)) not in seen, "connections: повторяющийся переход.")
        seen.add(tuple(sorted(edge)))
    seen = set()
    for f in document["facts"]:
        require(f["id"] not in seen, "facts: повторяющийся ID " + f["id"])
        seen.add(f["id"])
        require(
            all(i in entities and entities[i]["kind"] in {"hero", "npc"} for i in f["known_by"]),
            "facts." + f["id"] + ".known_by: неизвестный носитель знания.",
        )
        require(
            not f["resolved"] or f["status"] == "promise",
            "facts." + f["id"] + ": resolved только для обещания.",
        )
    return document


def default_document():
    w = initial_world()
    return WorldDocument.model_validate(
        dict(
            format="dnd-world@1",
            id="dry_well",
            version="1.0.0",
            title="Вода для деревни",
            lore="Небольшая деревня у леса. Приключение для ведущего и одного-двух новичков на 30–40 минут.",
            intro=w["intro"],
            gm_notes="Начни с просьбы Ады. Спроси игроков, что они хотят предпринять. Помогай выбрать подход, но не решай за них.",
            boundaries=[
                "Без жестокости. Бой необязателен.",
                "Причина исчезновения воды пока открыта; новые подробности принимает ведущий.",
            ],
            rules={"engine": "hybrid-simple@1", "manual_rules": []},
            entities=list(w["entities"].values()),
            facts=[{k: v for k, v in f.items() if k != "source"} for f in w["facts"].values()],
            connections=w["connections"],
        )
    ).model_dump()


def start_document(document):
    document = validate_world(WorldDocument.model_validate(document).model_dump())
    state = initial_world()
    state.update(
        document=deepcopy(document),
        intro=document["intro"],
        entities={
            e["id"]: {k: v for k, v in e.items() if k not in {"hp", "max_hp", "stats"} or v is not None}
            for e in document["entities"]
        },
        facts={f["id"]: {**f, "source": "author"} for f in document["facts"]},
        connections=deepcopy(document["connections"]),
    )
    return state


def restore_journal(journal):
    state = start_document(journal["document"])
    seen = set()
    for index, event in enumerate(journal["events"]):
        prefix = f"events.{index}: "
        require(
            event["id"] not in seen and event["id"] == event["action"]["id"],
            prefix + "повторный или несогласованный ID события.",
        )
        seen.add(event["id"])
        actor = event["action"]["actor"]
        require(
            actor in state["entities"] and state["entities"][actor]["kind"] == "hero",
            prefix + "неизвестный герой.",
        )
        require(
            event["action"]["mode"] != "hint" or not event["operations"] and not event["roll"],
            prefix + "совет не меняет мир.",
        )
        if event["roll"]:
            r = event["roll"]
            require(r["success"] == (r["value"] + r["bonus"] >= r["dc"]), prefix + "неверный итог броска.")
            if event["check"]:
                c = event["check"]
                require(
                    r["bonus"] == state["entities"][actor]["stats"][c["stat"]]
                    and r["dc"] == {"easy": 10, "standard": 13, "hard": 16}[c["difficulty"]],
                    prefix + "бросок не соответствует проверке.",
                )
        try:
            state = apply_operations(state, event["operations"], actor, event["id"])
        except GameError as exc:
            raise GameError(prefix + str(exc)) from exc
        state["events"].append(deepcopy(event))
        state["epoch"] += 1
    return state


def parse_document(text):
    require(isinstance(text, str) and len(text.encode("utf-8")) <= MAX_BYTES, "Нужен JSON до 500 КБ.")
    text = text.lstrip("\ufeff").strip()
    fence = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", text)
    if fence:
        text = fence[1]
    raw = json.loads(
        text,
        object_pairs_hook=_pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NaN/Infinity недопустимы.")),
    )
    require(isinstance(raw, dict), "Нужен один JSON-объект.")
    if raw.get("format") == "dnd-adventure@1":
        raise GameError(
            "format: это сценарий прежнего режима. Откройте его в «Подготовленных приключениях» или преобразуйте в dnd-world@1 с новым комплектом автора. Правила и условия автоматически не заменяются."
        )
    if raw.get("format") == "dnd-world-log@1":
        journal = Journal.model_validate(raw).model_dump()
        return journal, restore_journal(journal)
    document = WorldDocument.model_validate(raw).model_dump()
    return document, start_document(document)


def validation_report(text):
    errors, raw, state = [], None, None
    try:
        raw, state = parse_document(text)
    except ValidationError as exc:
        errors = [
            {"path": ".".join(map(str, e["loc"])), "message": e["msg"]}
            for e in exc.errors(include_input=False, include_url=False)[:30]
        ]
    except json.JSONDecodeError as exc:
        errors = [
            {
                "path": f"строка {exc.lineno}, столбец {exc.colno}",
                "message": "Некорректный JSON: проверьте запятые, кавычки и полноту документа.",
            }
        ]
    except (GameError, ValueError, RecursionError) as exc:
        errors = [
            {
                "path": "$",
                "message": "Слишком глубокий документ."
                if isinstance(exc, RecursionError)
                else str(exc)[:400],
            }
        ]
    report = dict(ok=not errors, errors=errors, preview=None, repair_prompt="")
    if state:
        report["preview"] = dict(
            title=state["document"]["title"],
            heroes=[e["name"] for e in state["entities"].values() if e["kind"] == "hero"],
            events=len(state["events"]),
            facts=len(state["facts"]),
            format=raw["format"],
            rules="hybrid-simple@1",
        )
    else:
        report["repair_prompt"] = (
            "Исправь документ по схеме dnd-world@1 (или журнал dnd-world-log@1). Сохрани лор, тайны и правила; неподдержанные правила явно укажи в manual_rules. Верни полный JSON. Ошибки:\n"
            + "\n".join(f"{e['path']}: {e['message']}" for e in errors)
        )
    return report


def load_document(text):
    report = validation_report(text)
    require(report["ok"], report["repair_prompt"])
    return parse_document(text)[1]


def export_journal(state):
    require(state is not None, "Сначала начните сессию.")
    # An unconfirmed proposal is deliberately absent. Legacy prototype started from this default.
    raw = dict(
        format="dnd-world-log@1", document=state.get("document") or default_document(), events=state["events"]
    )
    journal = Journal.model_validate(raw).model_dump()
    restored = restore_journal(journal)
    for key in ("entities", "facts", "connections"):
        require(
            restored[key] == state[key],
            "Журнал не воспроизводит состояние. Экспорт остановлен, исходное сохранение не изменено.",
        )
    require(
        len(json.dumps(journal, ensure_ascii=False).encode("utf-8")) <= MAX_BYTES,
        "Журнал превышает 500 КБ; локальная база сохранена.",
    )
    return journal


def example_document():
    return json.loads((Path(__file__).parent / "resources/example-world.json").read_text("utf-8"))


def author_kit():
    prompt = """Помоги мне подготовить короткую настольную игру для начинающего ведущего и 1–2 игроков.
Сначала обсуди лор, завязку, тайну, готовых героев, NPC с мотивами, места, возможные препятствия и границы импровизации.
Это помощник ЧЕЛОВЕКА-ведущего. Не составляй дерево всех ходов и обязательный маршрут. Игроки могут отказаться от задания.
Подготовь ведущему литературную опору, а не только конспект: lore — 3–5 небольших абзацев о том,
чем живёт место, его атмосфере, обычаях и назревающей проблеме (без тайны); gm_notes — связная фабула,
истинная причина событий, мотивы и манера речи NPC, 2–3 возможных подхода игроков, помощь новичку.
intro — готовое начало для чтения вслух, примерно 70–120 слов: образ места, событие, живая реплика NPC,
в конце простой вопрос игрокам. Описания мест добавляют запахи, звуки и характерные детали без скрытых улик.
Секреты остаются в gm_notes и gm-facts; литературный текст не заменяет факты и операции.
После согласования выдай один полный JSON dnd-world@1 по приложенной схеме и примеру, без заглушек и дополнительных полей.
Движок hybrid-simple@1: d20 + strength/agility/mind против 10/13/16; урон 2 (HP не ниже 1), лечение 3 с расходом перевязи.
Иные редакции и особые правила — только rules.manual_rules для ручного решения, не обещай их автоматическое исполнение.
entities: 1–4 hero с hp/max_hp/stats, location с location=null, npc с location и goal, item с владельцем или местом.
ID уникальны, латиница lowercase/цифры/подчёркивание, первая буква; связи — пары ID мест; имена различаются.
facts: известное, слухи и обещания разделены status; неизменные тайны visibility=gm; known_by только ID тех, кто знает.
Не помещай секреты в публичные описания. Запиши канон в facts, запреты импровизации в boundaries, помощь ведущему в gm_notes.
Если есть прошлый текстовый лог, перенеси подтверждённые ведущим события в facts, текущие HP/вещи/позиции в entities;
сомнения сохрани как claim, спроси о противоречиях. Не придумывай пропущенные броски и прошлые успехи.
Для продолжения сохранённой игры приложение принимает свой экспорт dnd-world-log@1, его не нужно переписывать моделью.
При ошибке валидации исправляй весь документ по отчёту приложения, сохраняя замысел.
"""
    return {
        "prompt": prompt,
        "schema": WorldDocument.model_json_schema(),
        "journal_schema": Journal.model_json_schema(),
        "example": example_document(),
    }
