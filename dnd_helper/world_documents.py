"""Local author documents and replayable GM journals. No model, code or network on import."""

import json
import re
from pathlib import Path
from copy import deepcopy
from typing import Annotated, Literal

from pydantic import Field, ValidationError

from .adventures import _pairs
from .free_world import (
    Advice,
    Check,
    Operation,
    Pressure,
    Strict,
    apply_operations,
    complete_authored_rules,
    initial_world,
)
from .rules import GameError, require

MAX_BYTES = 500_000
Id = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,49}$")]
Text = Annotated[str, Field(min_length=1, max_length=3000)]


class Rules(Strict):
    engine: Literal["intent-simple"] = "intent-simple"
    manual_rules: list[Text] = Field(default_factory=list, max_length=10)


class WorldEntity(Strict):
    id: Id
    kind: Literal["hero", "location", "npc", "item", "feature"]
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(max_length=500)
    location: Id | None
    goal: str = Field(default="", max_length=300)
    hp: int | None = Field(default=None, ge=0, le=100)
    max_hp: int | None = Field(default=None, ge=1, le=100)
    stats: dict[Literal["strength", "agility", "mind"], Annotated[int, Field(ge=-5, le=5)]] | None = None
    capabilities: list[Id] = Field(default_factory=list, max_length=20)
    state: str = Field(default="", max_length=50)
    states: list[str] = Field(default_factory=list, max_length=20)
    status: Literal["active", "dead"] | None = None


class WorldFact(Strict):
    id: Id
    text: str = Field(min_length=1, max_length=600)
    status: Literal["fact", "claim", "rumor", "promise", "task"]
    visibility: Literal["public", "gm"]
    known_by: list[Id] = Field(max_length=10)
    resolved: bool = False
    subjects: list[Id] = Field(default_factory=list, max_length=12)


class StoryThread(Strict):
    id: Id
    title: str = Field(min_length=1, max_length=120)
    stakes: str = Field(min_length=1, max_length=500)
    subjects: list[Id] = Field(min_length=1, max_length=12)
    pressure: int = Field(default=0, ge=0, le=2)
    resolved: bool = False
    outcome: str = Field(default="", max_length=500)


class Guardian(Strict):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)


class Discovery(Strict):
    id: Id
    fact: Id
    at: Id


class WorldCondition(Strict):
    source: Literal["entity", "fact", "thread", "session"]
    id: Id | None = None
    field: Literal["location", "state", "status", "resolved", "pressure", "ending"]
    value: str | int | bool | None
    negate: bool = False


class TransitionMethod(Strict):
    capability: Id
    check: Check | None = None


class Transition(Strict):
    id: Id
    entity: Id
    before: str = Field(min_length=1, max_length=50)
    after: str = Field(min_length=1, max_length=50)
    methods: list[TransitionMethod] = Field(min_length=1, max_length=10)
    when: list[WorldCondition] = Field(default_factory=list, max_length=12)
    match: Literal["all", "any"] = "all"


class ReactionSetState(Strict):
    effect: Literal["set_state"]
    entity: Id
    state: str = Field(min_length=1, max_length=50)


class ReactionResolveFact(Strict):
    effect: Literal["resolve_fact"]
    fact: Id


class ReactionAdvanceThread(Strict):
    effect: Literal["advance_thread"]
    thread: Id


class ReactionResolveThread(Strict):
    effect: Literal["resolve_thread"]
    thread: Id
    outcome: str = Field(min_length=1, max_length=500)


ReactionEffect = Annotated[
    ReactionSetState | ReactionResolveFact | ReactionAdvanceThread | ReactionResolveThread,
    Field(discriminator="effect"),
]


class WorldReaction(Strict):
    id: Id
    when: list[WorldCondition] = Field(min_length=1, max_length=12)
    match: Literal["all", "any"] = "all"
    effects: list[ReactionEffect] = Field(min_length=1, max_length=12)
    priority: int = Field(default=0, ge=-100, le=100)
    once: bool = True
    summary: str = Field(min_length=1, max_length=300)
    read_aloud: str = Field(min_length=1, max_length=800)
    halt: bool = False


class WorldEnding(Strict):
    id: Id
    when: list[WorldCondition] = Field(min_length=1, max_length=12)
    match: Literal["all", "any"] = "all"
    priority: int = Field(default=0, ge=-100, le=100)
    ending: Literal["victory", "costly_victory", "refusal", "defeat", "tragedy"]
    pressure_thread: Id
    resolve_facts: list[Id] = Field(default_factory=list, max_length=20)
    resolve_threads: list[Id] = Field(min_length=1, max_length=20)
    summary: str = Field(min_length=1, max_length=300)
    # A deterministic ending replaces AdviceOutcome.read_aloud and therefore
    # must obey the same limit.
    read_aloud: str = Field(min_length=1, max_length=800)


class GuardedRoute(Strict):
    between: list[Id] = Field(min_length=2, max_length=2)
    when: list[WorldCondition] = Field(min_length=1, max_length=12)
    match: Literal["all", "any"] = "all"
    blocked_text: str = Field(min_length=1, max_length=300)


class SceneOpportunity(Strict):
    id: Id
    title: str = Field(min_length=1, max_length=160)
    cue: str = Field(min_length=1, max_length=500)
    subjects: list[Id] = Field(min_length=1, max_length=6)
    supports: list[Id] = Field(default_factory=list, max_length=6)
    when: list[WorldCondition] = Field(default_factory=list, max_length=12)
    match: Literal["all", "any"] = "all"
    priority: int = Field(default=0, ge=-100, le=100)


class SceneFrame(Strict):
    id: Id
    at: Id
    goal: str = Field(min_length=1, max_length=300)
    obstacle: Id | None = None
    transitions: list[Id] = Field(default_factory=list, max_length=12)
    opportunities: list[SceneOpportunity] = Field(default_factory=list, max_length=12)


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
    discoveries: list[Discovery] = Field(default_factory=list, max_length=200)
    transitions: list[Transition] = Field(default_factory=list, max_length=200)
    reactions: list[WorldReaction] = Field(default_factory=list, max_length=100)
    endings: list[WorldEnding] = Field(default_factory=list, max_length=20)
    routes: list[GuardedRoute] = Field(default_factory=list, max_length=100)
    scenes: list[SceneFrame] = Field(default_factory=list, max_length=30)
    threads: list[StoryThread] = Field(min_length=1, max_length=20)
    guardian: Guardian | None = None


class JournalAction(Strict):
    id: str = Field(min_length=1, max_length=100)
    actor: Id
    text: str = Field(min_length=1, max_length=2000)
    mode: Literal["action", "hint", "manual"] = "action"
    participants: list[Id] = Field(default_factory=list, max_length=4)


class Roll(Strict):
    value: int = Field(ge=1, le=20)
    bonus: int = Field(ge=-5, le=7)
    dc: Literal[10, 13, 16]
    success: bool
    actor: Id | None = None
    stat_bonus: int | None = Field(default=None, ge=-5, le=5)
    assist_bonus: int = Field(default=0, ge=0, le=2)


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
    progress: Literal["meaningful", "no_progress"] = "meaningful"
    pressure: Pressure = Field(default_factory=Pressure)
    reactions: list[Id] = Field(default_factory=list, max_length=100)
    ending: Id | None = None
    protected_operations: list[int] = Field(default_factory=list, max_length=12)


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
            require(
                e["hp"] is not None and e["max_hp"] and 0 < e["hp"] <= e["max_hp"],
                path + ": готовому герою нужны 0 < hp ≤ max_hp.",
            )
            require(e["status"] in {None, "active"}, path + ": готовый герой должен быть active.")
            require(
                e["stats"] is not None and set(e["stats"]) == {"strength", "agility", "mind"},
                path + ".stats: нужны strength, agility, mind.",
            )
        else:
            require(
                all(e[k] is None for k in ("hp", "max_hp", "stats")),
                path + ": характеристики поддержаны только для героев.",
            )
            require(e["status"] is None, path + ": статус active/dead поддержан только для героев.")
        require(len(e["capabilities"]) == len(set(e["capabilities"])), path + ": возможности повторяются.")
        require(len(e["states"]) == len(set(e["states"])), path + ": состояния повторяются.")
        require(
            (not e["state"] and not e["states"]) or e["state"] in e["states"],
            path + ": текущее состояние должно входить в states.",
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
            not f["resolved"] or f["status"] in {"promise", "task"},
            "facts." + f["id"] + ": resolved только для обещания или задачи.",
        )
        require(
            all(i in entities for i in f["subjects"]),
            "facts." + f["id"] + ".subjects: неизвестный объект.",
        )
    seen = set()
    for discovery in document["discoveries"]:
        require(discovery["id"] not in seen, "discoveries: повторяющийся ID " + discovery["id"])
        seen.add(discovery["id"])
        require(discovery["fact"] in {f["id"] for f in document["facts"]}, "discoveries: неизвестный факт.")
        require(discovery["at"] in entities, "discoveries: неизвестный объект или место.")
    seen = set()
    for transition in document["transitions"]:
        require(transition["id"] not in seen, "transitions: повторяющийся ID " + transition["id"])
        seen.add(transition["id"])
        entity = entities.get(transition["entity"])
        require(entity is not None, "transitions: неизвестный объект.")
        require(
            transition["before"] in entity["states"] and transition["after"] in entity["states"],
            "transitions: состояния не объявлены у объекта.",
        )
        capabilities = [m["capability"] for m in transition["methods"]]
        require(len(capabilities) == len(set(capabilities)), "transitions: способы повторяются.")
        require(
            all(
                any(method["capability"] in provider["capabilities"] for provider in entities.values())
                for method in transition["methods"]
            ),
            "transitions." + transition["id"] + ": для способа нет носителя capability.",
        )
    thread_ids = set()
    for thread in document["threads"]:
        require(thread["id"] not in thread_ids, "threads: повторяющийся ID " + thread["id"])
        thread_ids.add(thread["id"])
        require(
            all(i in entities for i in thread["subjects"]),
            "threads." + thread["id"] + ".subjects: неизвестный объект.",
        )
        require(not thread["resolved"], "threads: сценарий должен начинаться с открытых нитей.")

    facts = {fact["id"]: fact for fact in document["facts"]}

    def validate_condition(condition, path):
        source, target, field, value = (
            condition["source"],
            condition.get("id"),
            condition["field"],
            condition.get("value"),
        )
        if source == "entity":
            require(target in entities, path + ": неизвестная сущность.")
            require(field in {"location", "state", "status"}, path + ": неверное поле сущности.")
            if field == "state":
                require(value in entities[target]["states"], path + ": состояние не объявлено у сущности.")
            elif field == "status":
                require(value in {"active", "dead", None}, path + ": неверный статус.")
            else:
                require(
                    value == "consumed" or value in entities,
                    path + ": неизвестное место или владелец.",
                )
        elif source == "fact":
            require(target in facts, path + ": неизвестный факт.")
            require(field == "resolved" and type(value) is bool, path + ": факт проверяет только resolved.")
        elif source == "thread":
            require(target in thread_ids, path + ": неизвестная сюжетная нить.")
            require(field in {"pressure", "resolved"}, path + ": неверное поле сюжетной нити.")
            require(
                type(value) is int and 0 <= value <= 3 if field == "pressure" else type(value) is bool,
                path + ": неверное значение сюжетной нити.",
            )
        else:
            require(target is None, path + ": условию сессии не нужен id.")
            require(field in {"status", "ending"}, path + ": неверное поле сессии.")
            require(
                value in {"active", "finished"}
                if field == "status"
                else value in {None, "victory", "costly_victory", "refusal", "defeat", "tragedy"},
                path + ": неверное значение сессии.",
            )

    for transition in document["transitions"]:
        for index, condition in enumerate(transition["when"]):
            validate_condition(condition, f"transitions.{transition['id']}.when.{index}")

    transition_ids = {transition["id"] for transition in document["transitions"]}

    def static_place(entity_id):
        entity = entities[entity_id]
        if entity["kind"] == "location":
            return entity_id
        parent = entities.get(entity["location"])
        return entity["location"] if not parent else static_place(parent["id"])

    scene_ids, scene_places, opportunity_ids = set(), set(), set()
    for scene in document["scenes"]:
        path = "scenes." + scene["id"]
        require(scene["id"] not in scene_ids, path + ": повторяющийся ID.")
        scene_ids.add(scene["id"])
        require(
            scene["at"] in entities and entities[scene["at"]]["kind"] == "location",
            path + ".at: нужна существующая локация.",
        )
        require(scene["at"] not in scene_places, path + ": для локации уже объявлена сцена.")
        scene_places.add(scene["at"])
        require(
            scene["obstacle"] is None
            or scene["obstacle"] in entities
            and static_place(scene["obstacle"]) == scene["at"],
            path + ".obstacle: препятствие должно находиться в этой сцене.",
        )
        require(
            len(scene["transitions"]) == len(set(scene["transitions"]))
            and all(item in transition_ids for item in scene["transitions"]),
            path + ".transitions: неизвестный или повторный переход.",
        )
        if scene["obstacle"]:
            require(
                any(
                    transition["id"] in scene["transitions"]
                    and transition["entity"] == scene["obstacle"]
                    for transition in document["transitions"]
                ),
                path + ": препятствие не изменяется переходом этой сцены.",
            )
            methods = {
                method["capability"]
                for transition in document["transitions"]
                if transition["id"] in scene["transitions"]
                for method in transition["methods"]
            }
            require(
                len(methods) >= 2,
                path + ": у центрального препятствия нужны хотя бы два разных подхода.",
            )
        for opportunity in scene["opportunities"]:
            item_path = path + ".opportunities." + opportunity["id"]
            require(opportunity["id"] not in opportunity_ids, item_path + ": повторяющийся ID.")
            opportunity_ids.add(opportunity["id"])
            require(
                all(subject in entities for subject in opportunity["subjects"]),
                item_path + ": неизвестный предмет возможности.",
            )
            require(
                all(static_place(subject) == scene["at"] for subject in opportunity["subjects"]),
                item_path + ": возможность должна быть физически в своей сцене.",
            )
            require(
                len(opportunity["supports"]) == len(set(opportunity["supports"]))
                and all(item in scene["transitions"] for item in opportunity["supports"]),
                item_path + ".supports: нужен переход этой сцены.",
            )
            for index, condition in enumerate(opportunity["when"]):
                validate_condition(condition, f"{item_path}.when.{index}")

    reaction_ids = set()
    reaction_state_entities = set()
    for reaction in document["reactions"]:
        path = "reactions." + reaction["id"]
        require(reaction["id"] not in reaction_ids, path + ": повторяющийся ID.")
        reaction_ids.add(reaction["id"])
        for index, condition in enumerate(reaction["when"]):
            validate_condition(condition, f"{path}.when.{index}")
        for effect in reaction["effects"]:
            if effect["effect"] == "set_state":
                reaction_state_entities.add(effect["entity"])
                require(effect["entity"] in entities, path + ": эффект ссылается на неизвестную сущность.")
                require(
                    effect["state"] in entities[effect["entity"]]["states"],
                    path + ": эффект задаёт необъявленное состояние.",
                )
            elif effect["effect"] == "resolve_fact":
                fact = facts.get(effect["fact"])
                require(
                    fact and fact["status"] in {"promise", "task"},
                    path + ": завершать можно только существующее обещание или задачу.",
                )
            else:
                require(effect["thread"] in thread_ids, path + ": эффект ссылается на неизвестную нить.")

    mutable_entities = {transition["entity"] for transition in document["transitions"]} | reaction_state_entities
    for entity in entities.values():
        require(
            len(entity["states"]) <= 1 or entity["id"] in mutable_entities,
            "entities."
            + entity["id"]
            + ": перечислены разные состояния, но ни transition, ни reaction не могут их изменить.",
        )

    ending_ids, priorities = set(), set()
    for ending in document["endings"]:
        path = "endings." + ending["id"]
        require(ending["id"] not in ending_ids, path + ": повторяющийся ID.")
        ending_ids.add(ending["id"])
        require(ending["priority"] not in priorities, path + ": приоритет финала должен быть уникальным.")
        priorities.add(ending["priority"])
        for index, condition in enumerate(ending["when"]):
            validate_condition(condition, f"{path}.when.{index}")
        require(
            all(fact in facts and facts[fact]["status"] in {"promise", "task"} for fact in ending["resolve_facts"]),
            path + ": финал завершает неизвестный факт или не задачу.",
        )
        require(
            len(ending["resolve_facts"]) == len(set(ending["resolve_facts"])),
            path + ": повторяющиеся задачи.",
        )
        require(
            all(thread in thread_ids for thread in ending["resolve_threads"]),
            path + ": финал завершает неизвестную нить.",
        )
        require(
            len(ending["resolve_threads"]) == len(set(ending["resolve_threads"])),
            path + ": повторяющиеся нити.",
        )
        require(
            ending["pressure_thread"] in ending["resolve_threads"],
            path + ": основная нить должна завершаться этим финалом.",
        )

    guarded = set()
    connections = {tuple(sorted(edge)) for edge in document["connections"]}
    for index, route in enumerate(document["routes"]):
        pair = tuple(sorted(route["between"]))
        require(pair in connections, f"routes.{index}: условный маршрут отсутствует в connections.")
        require(pair not in guarded, f"routes.{index}: повторяющееся условие маршрута.")
        guarded.add(pair)
        for condition_index, condition in enumerate(route["when"]):
            validate_condition(condition, f"routes.{index}.when.{condition_index}")
    return document


def default_document():
    w = initial_world()
    return WorldDocument.model_validate(
        dict(
            format="dnd-world@1",
            id="dry_well",
            version="1.0.0",
            title="Вода для деревни",
            lore="Небольшая деревня у леса. Законченное приключение для начинающего ведущего и компании до четырёх игроков.",
            intro=w["intro"],
            gm_notes="Начни с просьбы Ады. Спроси игроков, что они хотят предпринять. Помогай выбрать подход, но не решай за них.",
            boundaries=[
                "Без жестокости. Бой необязателен.",
                "Причина исчезновения воды пока открыта; новые подробности принимает ведущий.",
            ],
            rules={"engine": "intent-simple", "manual_rules": []},
            entities=list(w["entities"].values()),
            facts=[{k: v for k, v in f.items() if k != "source"} for f in w["facts"].values()],
            connections=w["connections"],
            discoveries=[],
            transitions=[],
            threads=list(w["threads"].values()),
            guardian={
                "name": "Хранитель Брода",
                "description": "Образ естественных последствий и движения общей беды, а не защитник единственного решения.",
            },
        )
    ).model_dump()


def start_document(document):
    document = validate_world(WorldDocument.model_validate(document).model_dump())
    state = initial_world()
    entities = {
        e["id"]: {k: v for k, v in e.items() if k not in {"hp", "max_hp", "stats", "status"} or v is not None}
        for e in document["entities"]
    }
    for entity in entities.values():
        if entity["kind"] == "hero":
            entity["status"] = "active"
    state.update(
        document=deepcopy(document),
        intro=document["intro"],
        entities=entities,
        facts={f["id"]: {**f, "source": "author"} for f in document["facts"]},
        connections=deepcopy(document["connections"]),
        observations={},
        fired_reactions=[],
        threads={thread["id"]: deepcopy(thread) for thread in document["threads"]},
        session={"status": "active", "ending": None},
    )
    return state


def restore_journal(journal):
    state = start_document(journal["document"])
    seen = set()
    for index, event in enumerate(journal["events"]):
        prefix = f"events.{index}: "
        require(state["session"]["status"] == "active", prefix + "сессия уже была завершена.")
        require(
            event["id"] not in seen and event["id"] == event["action"]["id"],
            prefix + "повторный или несогласованный ID события.",
        )
        seen.add(event["id"])
        actor = event["action"]["actor"]
        participants = event["action"].get("participants") or [actor]
        require(
            actor in state["entities"] and state["entities"][actor]["kind"] == "hero",
            prefix + "неизвестный герой.",
        )
        require(
            actor in participants
            and len(participants) == len(set(participants))
            and all(
                i in state["entities"]
                and state["entities"][i]["kind"] == "hero"
                and state["entities"][i].get("status", "active") == "active"
                for i in participants
            ),
            prefix + "неверный состав участников.",
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
                check_actor = c.get("actor") or actor
                helpers = c.get("helpers", [])
                stat_bonus = state["entities"][check_actor]["stats"][c["stat"]]
                assist_bonus = min(2, len(helpers))
                require(
                    check_actor in participants
                    and all(i in participants and i != check_actor for i in helpers)
                    and len(helpers) == len(set(helpers))
                    and r["bonus"] == stat_bonus + assist_bonus
                    and (r.get("actor") is None or r["actor"] == check_actor)
                    and (r.get("stat_bonus") is None or r["stat_bonus"] == stat_bonus)
                    and r.get("assist_bonus", 0) == assist_bonus
                    and r["dc"] == {"easy": 10, "standard": 13, "hard": 16}[c["difficulty"]],
                    prefix + "бросок не соответствует проверке.",
                )
        protected = event.get("protected_operations", [])
        require(
            protected == sorted(set(protected))
            and all(0 <= item < len(event["operations"]) for item in protected),
            prefix + "неверная карта обязательных операций.",
        )
        base_operations = [
            operation
            for operation_index, operation in enumerate(event["operations"])
            if operation_index not in set(protected)
        ]
        require(
            all(operation["op"] != "react_state" for operation in base_operations),
            prefix + "системная реакция выдана за обычную операцию.",
        )
        replay_proposal = Advice.model_validate(
            {
                "summary": event["summary"][:300] or "Событие",
                "gm_hint": (event.get("gm_hint") or "Проверьте событие.")[:400],
                "intent": {"kind": "other", "goal": event["action"]["text"][:300], "targets": []},
                "stop": "completed",
                "evidence": [],
                "question": None,
                "speaker": None,
                "check": None,
                "success": {
                    "summary": event["summary"][:300],
                    "read_aloud": event["text"][:800],
                    "operations": base_operations,
                },
                "failure": None,
            }
        )
        try:
            completed, authored = complete_authored_rules(state, event["action"], replay_proposal)
        except GameError as exc:
            raise GameError(prefix + str(exc)) from exc
        expected_operations = [operation.model_dump() for operation in completed.success.operations]
        expected_rules = authored.get("success", {})
        require(
            expected_operations == event["operations"]
            and expected_rules.get("reactions", []) == event.get("reactions", [])
            and expected_rules.get("ending") == event.get("ending")
            and expected_rules.get("protected", []) == protected,
            prefix + "обязательные последствия или финал не совпадают со сценарием.",
        )
        try:
            before = state
            state = apply_operations(state, event["operations"], actor, event["id"], participants)
        except GameError as exc:
            raise GameError(prefix + str(exc)) from exc
        irreversible = any(
            op["op"] == "finish_session" or op["op"] == "advance_thread" and op["after"] == 3
            for op in event["operations"]
        ) or any(
            before["entities"][i].get("status", "active") == "active"
            and state["entities"][i].get("status", "active") == "dead"
            for i in participants
        )
        require(
            not irreversible or event["pressure"]["level"] == "verdict",
            prefix + "необратимый исход не был обозначен как приговор.",
        )
        require(
            any(
                e["kind"] == "hero" and e.get("status", "active") == "active"
                for e in state["entities"].values()
            )
            or state["session"]["status"] == "finished",
            prefix + "гибель последнего героя не завершила сессию.",
        )
        declared_reactions = {
            reaction["id"]: reaction
            for reaction in state.get("document", {}).get("reactions", [])
        }
        require(
            len(event.get("reactions", [])) == len(set(event.get("reactions", [])))
            and all(item in declared_reactions for item in event.get("reactions", [])),
            prefix + "неизвестная или повторная обязательная реакция.",
        )
        declared_endings = {
            ending["id"] for ending in state.get("document", {}).get("endings", [])
        }
        require(
            event.get("ending") is None or event["ending"] in declared_endings,
            prefix + "неизвестный авторский финал.",
        )
        state["fired_reactions"] = list(
            dict.fromkeys(
                state.get("fired_reactions", [])
                + [
                    item
                    for item in event.get("reactions", [])
                    if declared_reactions[item].get("once", True)
                ]
            )
        )
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
            rules="intent-simple",
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
    for key in ("entities", "facts", "connections", "observations", "fired_reactions", "threads", "session"):
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
    prompt = """Помоги мне подготовить законченную настольную игру на 90–120 минут для начинающего ведущего и 1–4 игроков.
Сначала обсуди лор, завязку, тайну, готовых героев, NPC с мотивами, места, возможные препятствия и границы импровизации.
Это помощник ЧЕЛОВЕКА-ведущего. Не составляй дерево всех ходов и обязательный маршрут. Игроки могут отказаться от задания.
Подготовь ведущему литературную опору, а не только конспект: lore — 3–5 небольших абзацев о том,
чем живёт место, его атмосфере, обычаях и назревающей проблеме (без тайны); gm_notes — связная фабула,
истинная причина событий, мотивы и манера речи NPC, 2–3 возможных подхода игроков, помощь новичку.
intro — готовое начало для чтения вслух, примерно 70–120 слов: образ места, событие, живая реплика NPC,
в конце простой вопрос игрокам. Описания мест добавляют запахи, звуки и характерные детали без скрытых улик.
Секреты остаются в gm_notes и gm-facts; литературный текст не заменяет факты и операции.
После согласования выдай один полный JSON dnd-world@1 по приложенной схеме и примеру, без заглушек и дополнительных полей.
Движок intent-simple: одна заявка описывает значимую цель группы целиком. Выбери ведущего героя и участников;
обычно нужна не более чем одна основная проверка, каждый уместный помощник даёт +1, максимум +2. Безопасные подразумеваемые шаги и весь известный
маршрут выполняются одной транзакцией; новая заявка нужна только для риска, препятствия или содержательного выбора.
d20 + strength/agility/mind против 10/13/16; урон 2, при 0 HP герой окончательно погибает, лечение 3 с расходом перевязи.
Иные редакции и особые правила — только rules.manual_rules для ручного решения, не обещай их автоматическое исполнение.
entities: 1–4 hero с hp/max_hp/stats, location с location=null, npc с location и goal, item или feature с владельцем/местом.
capabilities — общие возможности вроде leverage, repair, cut; state/states — допустимые состояния важных объектов.
ID уникальны, латиница lowercase/цифры/подчёркивание, первая буква; связи — пары ID мест; имена различаются.
facts: знания, слухи, обещания и задачи разделены status; subjects связывает запись с сущностями; неизменные тайны
visibility=gm; known_by только ID тех, кто знает.
discoveries связывают скрытый факт с конкретным источником. Сам источник должен быть существующей entity с
понятным внешним описанием: ведущий или модель могут сначала вывести его в сцену, не раскрывая содержание.
Не заставляй игроков угадывать название объекта, но и не открывай факт от одного общего взгляда на комнату.
transitions объявляют причинные переходы состояния и допустимые методы-capabilities; не составляй дерево реплик.
Если у entity перечислено несколько states, для неё обязан существовать transition или reaction, способные
изменить state. Косметические состояния вроде «прочитано» не добавляй, если они не участвуют в механике.
reactions объявляют ОБЯЗАТЕЛЬНУЮ причинность, которую нельзя доверять памяти модели: when проверяет
entity/fact/thread/session, effects меняют состояние, закрывают задачу/нить или повышают давление. Добавь сюда
распад печати, срабатывание ловушки, достижение порога и подобные неизбежные последствия; once запрещает повтор.
endings задают машинно проверяемые условия всех существенных финалов, включая отказ: ending, pressure_thread,
закрываемые resolve_facts/resolve_threads, итог и готовую реплику. Не оставляй обязательный финал только в gm_notes.
routes задают условия доступности только для уже существующих пар connections; закрытый путь не предлагается.
scenes описывают не карту по метрам, а игровые зоны доступа. Для важной зоны укажи at, цель goal, реальный obstacle,
связанные transitions и обнаруживаемые opportunities. У opportunity есть безопасный cue для вывода в сцену,
физически находящиеся там subjects и supports — переходы, которым она помогает. Центральное препятствие должно
иметь хотя бы два разных метода. Не прячь все способы его преодоления за самим препятствием. Провал обязан менять
риск, доступ или показывать новую возможность; нельзя возвращать ту же неизменную сцену для повторной попытки.
Приоритеты endings должны быть уникальны. Условия — это сравнения уже объявленных полей, не свободный текст.
У каждой центральной проблемы должны быть конкретные обнаруживаемые сведения, хотя бы два разумных способа действия
и StoryThread со ставкой. Давление идёт от 0 до 3: знак, осложнение и подтверждаемый необратимый приговор.
guardian необязателен: если он есть, опиши его в мире как Хранителя причинности и темпа, а не защитника единственного сюжета.
Подготовь endings для логичного victory/costly_victory/refusal/defeat/tragedy; одна сессия не переносится на завтра.
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
