"""Conversational GM session with semantic model effects and local mechanics."""

import secrets
import os
import re
import threading
import time
from copy import deepcopy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .codex_provider import CodexProvider
from .engine import uid
from .rules import GameError, require
from .storage import Store, encode


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Entity(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,49}$")
    kind: Literal["location", "npc", "item", "feature"]
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(max_length=500)
    location: str  # Parent location for a new place; otherwise current holder/place.
    goal: str = Field(max_length=300)
    capabilities: list[str] = Field(default_factory=list, max_length=20)
    state: str = Field(default="", max_length=50)
    states: list[str] = Field(default_factory=list, max_length=20)


class Create(Strict):
    op: Literal["create"]
    entity: Entity


class Move(Strict):
    op: Literal["move"]
    entity: str
    before: str
    destination: str


class MovePath(Strict):
    op: Literal["move_path"]
    entity: str
    route: list[str] = Field(min_length=2, max_length=20)


class Transfer(Strict):
    op: Literal["transfer"]
    item: str
    before: str
    destination: str


class Consume(Strict):
    op: Literal["consume"]
    item: str
    owner: str


class Vital(Strict):
    op: Literal["damage", "heal"]
    entity: str


class Remember(Strict):
    op: Literal["remember"]
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,49}$")
    text: str = Field(min_length=1, max_length=600)
    status: Literal["fact", "claim", "rumor", "promise"]
    visibility: Literal["public", "gm"]
    known_by: list[str] = Field(max_length=10)
    subjects: list[str] = Field(default_factory=list, max_length=12)


class Resolve(Strict):
    op: Literal["resolve_promise"]
    id: str


class SetState(Strict):
    op: Literal["set_state"]
    entity: str
    before: str
    after: str
    method: str
    using: list[str] = Field(default_factory=list, max_length=10)


class ReactState(Strict):
    op: Literal["react_state"]
    reaction: str = Field(pattern=r"^[a-z][a-z0-9_]{0,49}$")
    entity: str
    before: str
    after: str


class Discover(Strict):
    op: Literal["discover"]
    fact: str
    knowers: list[str] = Field(min_length=1, max_length=10)


class ShareFact(Strict):
    op: Literal["share_fact"]
    fact: str
    source: str
    recipients: list[str] = Field(min_length=1, max_length=10)


class Observe(Strict):
    op: Literal["observe"]
    subject: str


class Present(Strict):
    op: Literal["present"]
    subject: str


class AdvanceThread(Strict):
    op: Literal["advance_thread"]
    thread: str
    before: int = Field(ge=0, le=2)
    after: int = Field(ge=1, le=3)


class ResolveThread(Strict):
    op: Literal["resolve_thread"]
    thread: str
    outcome: str = Field(min_length=1, max_length=500)


class FinishSession(Strict):
    op: Literal["finish_session"]
    ending: Literal["victory", "costly_victory", "refusal", "defeat", "tragedy"]
    summary: str = Field(min_length=1, max_length=800)


Operation = Annotated[
    Create
    | Move
    | MovePath
    | Transfer
    | Consume
    | Vital
    | Remember
    | Resolve
    | SetState
    | ReactState
    | Discover
    | ShareFact
    | Observe
    | Present
    | AdvanceThread
    | ResolveThread
    | FinishSession,
    Field(discriminator="op"),
]


class Outcome(Strict):
    summary: str = Field(min_length=1, max_length=800)
    operations: list[Operation] = Field(max_length=12)


class Check(Strict):
    stat: Literal["strength", "agility", "mind"]
    difficulty: Literal["easy", "standard", "hard"]
    actor: str | None = None
    helpers: list[str] = Field(default_factory=list, max_length=3)


class Intent(Strict):
    kind: Literal["observe", "travel", "communicate", "manipulate", "acquire", "other"]
    goal: str = Field(min_length=1, max_length=300)
    targets: list[str] = Field(max_length=10)


class Proposal(Strict):
    summary: str = Field(min_length=1, max_length=800)
    evidence: list[str] = Field(max_length=20)
    question: str | None
    speaker: str | None
    check: Check | None
    success: Outcome
    failure: Outcome | None


class Pressure(Strict):
    level: Literal["ordinary", "sign", "complication", "verdict"] = "ordinary"
    thread: str | None = None
    reason: str = Field(default="", max_length=400)


class AdviceOutcome(Outcome):
    summary: str = Field(min_length=1, max_length=300)
    read_aloud: str = Field(max_length=800)


class Advice(Proposal):
    summary: str = Field(min_length=1, max_length=300)
    gm_hint: str = Field(min_length=1, max_length=400)
    intent: Intent
    stop: Literal["completed", "blocked", "choice", "check", "advice", "finished"]
    progress: Literal["meaningful", "no_progress"] = "meaningful"
    pressure: Pressure = Field(default_factory=Pressure)
    success: AdviceOutcome
    failure: AdviceOutcome | None


# The model speaks in scene-sized meanings.  Exact routes, previous states and
# transition methods are derived from the canonical world below.  This keeps the
# journal strict without making the language model imitate the journal format.
class PresentEffect(Strict):
    effect: Literal["present"]
    entity: str


class RevealEffect(Strict):
    effect: Literal["reveal"]
    fact: str
    knowers: list[str] = Field(default_factory=list, max_length=4)


class ShareEffect(Strict):
    effect: Literal["share"]
    fact: str
    source: str
    recipients: list[str] = Field(min_length=1, max_length=4)


class TransitionEffect(Strict):
    effect: Literal["transition"]
    transition: str
    using: list[str] = Field(default_factory=list, max_length=4)


class TravelEffect(Strict):
    effect: Literal["travel"]
    entity: str
    destination: str


class TransferEffect(Strict):
    effect: Literal["transfer"]
    item: str
    destination: str


class ConsumeEffect(Strict):
    effect: Literal["consume"]
    item: str
    owner: str


class VitalEffect(Strict):
    effect: Literal["harm", "heal"]
    entity: str


class MemoryEffect(Strict):
    effect: Literal["remember"]
    text: str = Field(min_length=1, max_length=600)
    status: Literal["fact", "claim", "rumor", "promise"]
    visibility: Literal["public", "gm"]
    known_by: list[str] = Field(default_factory=list, max_length=4)
    subjects: list[str] = Field(default_factory=list, max_length=8)


class ResolveFactEffect(Strict):
    effect: Literal["resolve_fact"]
    fact: str


class AdvanceEffect(Strict):
    effect: Literal["advance_thread"]
    thread: str


class ResolveStoryEffect(Strict):
    effect: Literal["resolve_thread"]
    thread: str
    outcome: str = Field(min_length=1, max_length=500)


class FinishEffect(Strict):
    effect: Literal["finish"]
    ending: Literal["victory", "costly_victory", "refusal", "defeat", "tragedy"]
    summary: str = Field(min_length=1, max_length=800)


SemanticEffect = Annotated[
    PresentEffect
    | RevealEffect
    | ShareEffect
    | TransitionEffect
    | TravelEffect
    | TransferEffect
    | ConsumeEffect
    | VitalEffect
    | MemoryEffect
    | ResolveFactEffect
    | AdvanceEffect
    | ResolveStoryEffect
    | FinishEffect,
    Field(discriminator="effect"),
]


class DirectorOutcome(Strict):
    summary: str = Field(min_length=1, max_length=800)
    read_aloud: str = Field(max_length=800)
    effects: list[SemanticEffect] = Field(default_factory=list, max_length=10)


class DirectorCard(Strict):
    summary: str = Field(min_length=1, max_length=500)
    gm_hint: str = Field(default="", max_length=500)
    intent: Intent
    stop: Literal["completed", "blocked", "choice", "check", "advice", "finished"]
    progress: Literal["meaningful", "no_progress"] = "meaningful"
    evidence: list[str] = Field(default_factory=list, max_length=12)
    question: str | None
    speaker: str | None
    check: Check | None
    success: DirectorOutcome
    failure: DirectorOutcome | None


DIRECTOR = """Ты — соавтор человека-ведущего в живой настольной игре. Разреши намерение игрока целиком,
останавливаясь только перед риском, выбором или реальным препятствием. Не говори за героев и не решай за игроков.

Контекст — самодостаточная папка одного хода. visible_entities физически находятся в сцене и уже представлены игрокам.
known_entities только известны по рассказам или памяти и могут находиться далеко: не описывай рядом движение или позу такого объекта.
possible_introductions физически находятся здесь, но ещё не представлены: их можно ввести только через present, но нельзя советовать
игроку обратиться к ним как к уже известным.
known_facts известны героям. private_constraints — не реплики и не знания игроков: не раскрывай их без явного основания.
То, что NPC знает, не означает, что он хочет это сообщить. Следуй его цели и публичным заявлениям; при сомнении уклоняйся, а не выдавай тайну.

scene_frame задаёт цель сцены, реальное препятствие и авторские opportunities. Это возможности, а не готовые решения.
При осмотре или поиске выведи уместную свежую opportunity через её cue и present одного subject, не раскрывая скрытый ответ.
momentum_opportunity особенно важна после провала или пустого хода, если игрок не выбрал другой ясный подход.
Не создавай прозой новую запертую дверь, охрану или запрет маршрута: обязательным препятствием может быть только obstacle,
blocked_route или mechanical_option из папки. Если один заявленный переход открывает blocked_route и игрок явно хочет пройти,
включи transition и travel в одну карточку. Остальные импровизированные детали не блокируют действие и не повышают сложность.

travel_options — подготовленные приложением варианты целого пути. Для travel выбирай смысловую конечную цель из destination,
а не промежуточную транзитную точку. Если reaches_goal=false, всё равно укажи дальнюю цель: приложение само проведёт героя
по safe_route и остановит у первого настоящего препятствия. Не заканчивай ход на транзитном месте только потому, что игрок
упомянул выход на улицу, дорогу, коридор или другую часть пути.

mechanical_options уже вычислены приложением. Если намерению соответствует transition, выбери его ID и указанное using; не изобретай другую проверку.
Код подставит точную механику. При риске дай обе содержательно разные ветки; бросок не может вести к одинаковому исходу.
Гипотетический вопрос, условная реплика или обсуждение будущего действия не выполняют transition: ответь разговором или уточнением, оставив effects пустым.

present показывает объект; reveal открывает только available_discovery; transition выбирает переход; travel/transfer/consume меняют место или владение;
remember фиксирует новую договорённость; advance_thread/resolve_thread/finish меняют ставки. Если изменение мира не нужно, effects пуст.
Для mode=hint: stop=advice, без check и effects. director_note обязательна, если не ломает канон. read_aloud — 1–4 коротких живых предложения.
Верни только ответ по схеме."""


def initial_world():
    entities = {
        "square": dict(
            id="square",
            kind="location",
            name="Деревенская площадь",
            description="У колодца стоит пустое ведро. Тропа ведёт к лесу, рядом мастерская.",
            location=None,
            goal="",
        ),
        "workshop": dict(
            id="workshop",
            kind="location",
            name="Мастерская Ады",
            description="Деревянный навес с рабочим столом.",
            location=None,
            goal="",
        ),
        "forest": dict(
            id="forest",
            kind="location",
            name="Лесная тропа",
            description="Тропа уходит от деревни между соснами.",
            location=None,
            goal="",
        ),
        "ada": dict(
            id="ada",
            kind="npc",
            name="Ада",
            description="Мастер по водяным механизмам.",
            location="square",
            goal="Вернуть воду в деревню; ценит помощь и честность.",
        ),
        "mira": dict(
            id="mira",
            kind="hero",
            name="Мира",
            description="Любознательная следопытка.",
            location="square",
            goal="",
            hp=8,
            max_hp=8,
            stats={"strength": 1, "agility": 2, "mind": 1},
            status="active",
        ),
        "rope": dict(
            id="rope",
            kind="item",
            name="Верёвка",
            description="Крепкая верёвка длиной десять метров.",
            location="mira",
            goal="",
        ),
        "bandage": dict(
            id="bandage",
            kind="item",
            name="Перевязь",
            description="Одна чистая перевязь.",
            location="mira",
            goal="",
        ),
        "bucket": dict(
            id="bucket",
            kind="item",
            name="Ведро",
            description="Пустое ведро у колодца.",
            location="square",
            goal="",
        ),
    }
    facts = {
        "water": dict(
            id="water",
            text="С утра в деревенском колодце нет воды. Причина пока не установлена.",
            status="fact",
            visibility="public",
            known_by=["mira", "ada"],
            source="author",
            resolved=False,
            subjects=["square"],
        ),
        "help": dict(
            id="help",
            text="Ада просит помощи с водой. Мира вправе отказаться и уйти исследовать окрестности.",
            status="fact",
            visibility="public",
            known_by=["mira", "ada"],
            source="author",
            resolved=False,
            subjects=["ada", "square"],
        ),
    }
    return dict(
        id=uid(),
        revision=0,
        epoch=0,
        entities=entities,
        facts=facts,
        connections=[["square", "workshop"], ["square", "forest"]],
        observations={},
        fired_reactions=[],
        threads={
            "village_water": {
                "id": "village_water",
                "title": "Вода для деревни",
                "stakes": "К вечеру деревне понадобится вода; любой реальный способ решить проблему считается прогрессом.",
                "subjects": ["square", "ada"],
                "pressure": 0,
                "resolved": False,
                "outcome": "",
            }
        },
        session={"status": "active", "ending": None},
        director_note="",
        events=[],
        pending=None,
        calls=[],
        created=time.time(),
        intro="У колодца вас встречает Ада: «С утра ни капли. Поможете разобраться?»",
    )


def entity_place(entities, entity_id):
    entity = entities.get(entity_id)
    if not entity:
        return None
    if entity["kind"] == "location":
        return entity["id"]
    holder = entities.get(entity.get("location"))
    return entity.get("location") if not holder else entity_place(entities, holder["id"])


def world_condition_matches(world, condition):
    source, target, field = condition["source"], condition.get("id"), condition["field"]
    if source == "entity":
        actual = world["entities"][target].get(field)
    elif source == "fact":
        actual = world["facts"][target].get(field)
    elif source == "thread":
        actual = world["threads"][target].get(field)
    else:
        actual = world.get("session", {}).get(field)
    matched = actual == condition.get("value")
    return not matched if condition.get("negate", False) else matched


def world_conditions_match(world, conditions, mode="all"):
    results = [world_condition_matches(world, condition) for condition in conditions]
    return any(results) if mode == "any" else all(results)


def route_passable(world, left, right):
    pair = {left, right}
    for route in world.get("document", {}).get("routes", []):
        if set(route["between"]) == pair:
            return world_conditions_match(world, route["when"], route.get("match", "all"))
    return True


def shortest_path(world, start, destination, actor=None):
    if start == destination:
        return [start]
    graph = {}
    for left, right in world["connections"]:
        if not route_passable(world, left, right):
            continue
        graph.setdefault(left, []).append(right)
        graph.setdefault(right, []).append(left)
    queue = [[start]]
    seen = {start}
    for path in queue:
        for node in graph.get(path[-1], []):
            if node in seen:
                continue
            next_path = path + [node]
            if node == destination:
                return next_path
            seen.add(node)
            queue.append(next_path)
    return None


def structural_path(world, start, destination):
    """Find a route in the authored map without pretending guarded edges are open."""
    if start == destination:
        return [start]
    graph = {}
    for left, right in world["connections"]:
        graph.setdefault(left, []).append(right)
        graph.setdefault(right, []).append(left)
    queue = [[start]]
    seen = {start}
    for path in queue:
        for node in graph.get(path[-1], []):
            if node in seen:
                continue
            next_path = path + [node]
            if node == destination:
                return next_path
            seen.add(node)
            queue.append(next_path)
    return None


def travel_plan(world, start, destination, actor=None):
    """Resolve a whole journey or its maximal safe prefix up to a real obstacle."""
    route = shortest_path(world, start, destination, actor)
    if route:
        return {"route": route, "reached": True, "blocked": None}
    authored = structural_path(world, start, destination)
    if not authored:
        return None
    safe = [authored[0]]
    blocked = None
    for left, right in zip(authored, authored[1:]):
        if route_passable(world, left, right):
            safe.append(right)
            continue
        rule = next(
            (
                item
                for item in world.get("document", {}).get("routes", [])
                if set(item["between"]) == {left, right}
            ),
            None,
        )
        blocked = {
            "from": left,
            "destination": right,
            "reason": rule["blocked_text"] if rule else "Дальнейший путь сейчас закрыт.",
        }
        break
    return {"route": safe, "reached": False, "blocked": blocked}


def available_discoveries(world, actor, targets=None):
    """Return authored facts a current observation may reveal without another game step."""
    targets = set(targets or [])
    location = world["entities"][actor]["location"]
    result = []
    for discovery in world.get("document", {}).get("discoveries", []):
        fact = world["facts"].get(discovery["fact"])
        at = discovery["at"]
        if not fact or actor in fact["known_by"] or entity_place(world["entities"], at) != location:
            continue
        if targets and at not in targets and discovery["fact"] not in targets and location not in targets:
            continue
        result.append(discovery)
    return result


def transition_for(world, operation):
    for transition in world.get("document", {}).get("transitions", []):
        if (
            transition["entity"] == operation["entity"]
            and transition["before"] == operation["before"]
            and transition["after"] == operation["after"]
        ):
            for method in transition["methods"]:
                if method["capability"] == operation["method"]:
                    return transition, method
    return None, None


def _check_signature(check):
    if not check:
        return None
    data = check.model_dump() if isinstance(check, BaseModel) else check
    return data["stat"], data["difficulty"]


def _fold_words(text):
    return re.findall(r"[\w-]+", text.casefold().replace("ё", "е"))


def _entity_mentioned(entity, text):
    """Recognize a named entity without requiring Russian nominative case."""
    folded = text.casefold().replace("ё", "е")
    entity_id = entity["id"].casefold().replace("ё", "е")
    name = entity["name"].casefold().replace("ё", "е")
    if entity_id in folded or name in folded:
        return True
    action_words = [word for word in _fold_words(folded) if len(word) >= 5]
    name_words = [word for word in _fold_words(name) if len(word) >= 5]
    return any(
        action.startswith(name_word[:5]) or name_word.startswith(action[:5])
        for action in action_words
        for name_word in name_words
    )


def compile_semantic_outcome(world, action, outcome, check, branch):
    """Turn scene-sized model effects into exact, replayable journal operations."""
    actor = action["actor"]
    participants = action.get("participants") or [actor]
    operations = []
    projected = world
    for index, raw in enumerate(outcome.effects):
        effect = raw.model_dump() if isinstance(raw, BaseModel) else raw
        kind = effect["effect"]
        candidate = []
        entities = projected["entities"]
        if kind == "present":
            subject = entities.get(effect["entity"])
            require(subject is not None, "Модель попыталась показать неизвестный объект.")
            if projected.get("observations", {}).get(actor, {}).get(subject["id"]) != subject.get("state", ""):
                candidate = [{"op": "present", "subject": subject["id"]}]
        elif kind == "reveal":
            fact = projected["facts"].get(effect["fact"])
            require(fact is not None, "Модель попыталась открыть неизвестное сведение.")
            knowers = effect["knowers"] or participants
            if all(i in fact["known_by"] for i in knowers):
                continue
            require(
                any(
                    discovery["fact"] == effect["fact"]
                    and entity_place(entities, discovery["at"]) == entities[actor]["location"]
                    for discovery in projected.get("document", {}).get("discoveries", [])
                ),
                "Это сведение не является доступной находкой текущей сцены.",
            )
            candidate = [{"op": "discover", "fact": effect["fact"], "knowers": knowers}]
        elif kind == "share":
            candidate = [
                {
                    "op": "share_fact",
                    "fact": effect["fact"],
                    "source": effect["source"],
                    "recipients": effect["recipients"],
                }
            ]
        elif kind == "transition":
            transition = next(
                (
                    item
                    for item in projected.get("document", {}).get("transitions", [])
                    if item["id"] == effect["transition"]
                ),
                None,
            )
            require(transition is not None, "Модель выбрала неизвестный переход состояния.")
            require(
                world_conditions_match(
                    projected, transition.get("when", []), transition.get("match", "all")
                ),
                "Переход пока недоступен по состоянию мира.",
            )
            entity = entities.get(transition["entity"])
            require(entity and entity.get("state", "") == transition["before"], "Переход уже недоступен.")
            nearby = {
                entity_id
                for entity_id, item in entities.items()
                if entity_place(entities, entity_id) == entities[actor]["location"]
            }
            introduced = set(participants)
            introduced.update(
                entity_id
                for participant in participants
                for entity_id in projected.get("observations", {}).get(participant, {})
            )
            introduced.update(
                entity_id for entity_id, item in entities.items() if item.get("location") in participants
            )
            named = {
                entity_id
                for entity_id in nearby
                if _entity_mentioned(entities[entity_id], action["text"])
            }
            allowed = introduced | named
            requested = [entity_id for entity_id in effect["using"] if entity_id in allowed]
            expected_check = _check_signature(check)
            choices = [
                (method, entity_id)
                for method in transition["methods"]
                for entity_id in sorted(allowed & nearby)
                if method["capability"] in entities[entity_id].get("capabilities", [])
            ]
            require(choices, "Для перехода нет доступного способа.")
            choices.sort(
                key=lambda item: (
                    item[1] not in requested,
                    item[1] not in named,
                    _check_signature(item[0].get("check")) != expected_check,
                    item[0]["capability"],
                    item[1],
                )
            )
            choice, provider = choices[0]
            candidate = [
                {
                    "op": "set_state",
                    "entity": transition["entity"],
                    "before": transition["before"],
                    "after": transition["after"],
                    "method": choice["capability"],
                    "using": [provider],
                }
            ]
        elif kind == "travel":
            entity = entities.get(effect["entity"])
            require(entity and entity["kind"] in {"hero", "npc"}, "Неизвестный участник перемещения.")
            destination = entities.get(effect["destination"])
            require(destination and destination["kind"] == "location", "Неизвестная цель перемещения.")
            plan = travel_plan(
                projected, entity["location"], destination["id"], entity["id"]
            )
            require(plan is not None, "Выбранное место не связано с текущей областью мира.")
            require(
                len(plan["route"]) > 1 or not plan["reached"],
                "Герой уже находится в выбранном месте.",
            )
            if len(plan["route"]) > 1:
                candidate = [{"op": "move_path", "entity": entity["id"], "route": plan["route"]}]
            if not plan["reached"]:
                endpoint = entities[plan["route"][-1]]
                obstacle = plan["blocked"] or {
                    "from": endpoint["id"],
                    "destination": destination["id"],
                    "reason": "Дальнейший путь сейчас закрыт.",
                }
                if candidate:
                    operations.extend(candidate)
                    projected = apply_operations(
                        world, operations, actor, action["id"], participants
                    )
                return {
                    "summary": (
                        f"{entity['name']} добирается до места «{endpoint['name']}» и "
                        "останавливается перед препятствием."
                    ),
                    "read_aloud": (
                        f"{entity['name']} добирается до места «{endpoint['name']}». "
                        f"{obstacle['reason']}"
                    ),
                    "operations": operations,
                    "travel_stop": {
                        "goal": destination["id"],
                        "at": endpoint["id"],
                        **obstacle,
                    },
                }
        elif kind == "transfer":
            item = entities.get(effect["item"])
            require(item and item["kind"] == "item", "Модель выбрала неизвестный предмет.")
            candidate = [
                {
                    "op": "transfer",
                    "item": item["id"],
                    "before": item["location"],
                    "destination": effect["destination"],
                }
            ]
        elif kind == "consume":
            candidate = [{"op": "consume", "item": effect["item"], "owner": effect["owner"]}]
        elif kind in {"harm", "heal"}:
            candidate = [{"op": "damage" if kind == "harm" else "heal", "entity": effect["entity"]}]
        elif kind == "remember":
            known_by = effect["known_by"] or (participants if effect["visibility"] == "public" else [])
            candidate = [
                {
                    "op": "remember",
                    "id": f"memory_{action['id'][:12]}_{branch}_{index}",
                    "text": effect["text"],
                    "status": effect["status"],
                    "visibility": effect["visibility"],
                    "known_by": known_by,
                    "subjects": effect["subjects"],
                }
            ]
        elif kind == "resolve_fact":
            candidate = [{"op": "resolve_promise", "id": effect["fact"]}]
        elif kind == "advance_thread":
            thread = projected.get("threads", {}).get(effect["thread"])
            require(thread and thread["pressure"] < 3, "Сюжетную нить нельзя усилить ещё раз.")
            candidate = [
                {
                    "op": "advance_thread",
                    "thread": thread["id"],
                    "before": thread["pressure"],
                    "after": thread["pressure"] + 1,
                }
            ]
        elif kind == "resolve_thread":
            candidate = [
                {"op": "resolve_thread", "thread": effect["thread"], "outcome": effect["outcome"]}
            ]
        elif kind == "finish":
            if not any(item["resolved"] for item in projected.get("threads", {}).values()):
                open_threads = [item for item in projected.get("threads", {}).values() if not item["resolved"]]
                require(open_threads, "Перед финалом нужна объявленная сюжетная нить.")
                thread = sorted(open_threads, key=lambda item: (-item["pressure"], item["id"]))[0]
                candidate.append(
                    {"op": "resolve_thread", "thread": thread["id"], "outcome": effect["summary"]}
                )
            candidate.append(
                {"op": "finish_session", "ending": effect["ending"], "summary": effect["summary"]}
            )
        else:
            raise GameError("Модель вернула неизвестный смысловой эффект.")

        if not candidate:
            continue
        operations.extend(candidate)
        projected = apply_operations(world, operations, actor, action["id"], participants)
    return {
        "summary": outcome.summary,
        "read_aloud": outcome.read_aloud,
        "operations": operations,
    }


def compile_director_card(world, action, card, context=None):
    """Compile a flexible director card into the strict local transaction view."""
    success = compile_semantic_outcome(world, action, card.success, card.check, "success")
    failure = (
        compile_semantic_outcome(world, action, card.failure, card.check, "failure")
        if card.failure
        else None
    )
    travel_stop = success.pop("travel_stop", None)
    if failure:
        failure.pop("travel_stop", None)
    transition_checks = []
    transition_operations = [
        operation
        for outcome in (success, failure)
        if outcome
        for operation in outcome["operations"]
        if operation["op"] == "set_state"
    ]
    for operation in transition_operations:
        _, method = transition_for(world, operation)
        require(method is not None, "Приложение не нашло объявленный способ перехода.")
        transition_checks.append(method.get("check"))
    signatures = {_check_signature(item) for item in transition_checks}
    require(len(signatures) <= 1, "Один бросок не может обслуживать разные проверки.")
    check = card.check.model_dump() if card.check else None
    stop = card.stop
    speaker = card.speaker
    question = card.question
    if transition_operations:
        declared = transition_checks[0]
        if declared:
            require(failure is not None, "Для рискованного перехода нужна ветка неудачи.")
            check = {
                "stat": declared["stat"],
                "difficulty": declared["difficulty"],
                "actor": check.get("actor") if check else action["actor"],
                "helpers": check.get("helpers", []) if check else [],
            }
            stop = "check"
        else:
            check = None
            failure = None
            stop = "completed" if stop == "check" else stop
    if travel_stop and not check:
        # The intended destination exists, but a declared obstacle is the next
        # meaningful beat.  Keep the safe journey and do not roll for actions
        # that could only happen beyond that obstacle.
        failure = None
        stop = "blocked"
        speaker = None
        question = None
    proposal = Advice.model_validate(
        {
            "summary": card.summary,
            "gm_hint": card.gm_hint or "Проверьте реплику и последствия перед подтверждением.",
            "intent": card.intent.model_dump(),
            "stop": stop,
            "progress": card.progress,
            "pressure": {"level": "ordinary", "thread": None, "reason": ""},
            "evidence": card.evidence,
            "question": question,
            "speaker": speaker,
            "check": check,
            "success": success,
            "failure": failure,
        }
    )
    if travel_stop:
        data = proposal.model_dump()
        data["progress"] = "meaningful"
        data["gm_hint"] = _join_text(
            data["gm_hint"],
            "Безопасная часть пути выполнена целиком; следующий выбор начинается у настоящего препятствия.",
            500,
        )
        proposal = Advice.model_validate(data)
    proposal = complete_routine_steps(world, action["actor"], proposal)
    return apply_scene_momentum(world, action, proposal, context or {})


def apply_scene_momentum(world, action, proposal, context):
    """Use authored cues to keep exploration and failed checks from returning a static scene."""
    if action.get("mode") == "hint":
        return proposal
    opportunities = (context.get("scene_frame") or {}).get("fresh_opportunities", [])
    if not opportunities:
        return proposal
    data = proposal.model_dump()
    observed = world.get("observations", {}).get(action["actor"], {})

    def introduce(outcome):
        if not outcome:
            return False
        if any(op["op"] in {"present", "discover", "observe"} for op in outcome["operations"]):
            return False
        opportunity = opportunities[0]
        subject = next((item for item in opportunity["subjects"] if item not in observed), None)
        if not subject:
            return False
        outcome["operations"].append({"op": "present", "subject": subject})
        outcome["read_aloud"] = _join_text(outcome["read_aloud"], opportunity["cue"], 800)
        outcome["summary"] = (outcome["summary"] + " Показана новая возможность.")[:300]
        return True

    changed = False
    if data["intent"]["kind"] == "observe":
        substantive = any(
            op["op"] not in {"advance_thread"} for op in data["success"]["operations"]
        )
        if not substantive:
            changed = introduce(data["success"])
            if changed:
                data["progress"] = "meaningful"
    if data.get("failure") and not any(
        op["op"] in {"present", "discover", "observe", "set_state", "move", "move_path", "transfer"}
        for op in data["failure"]["operations"]
    ) and not any(
        op["op"] == "advance_thread" and op["after"] == 3
        for op in data["failure"]["operations"]
    ):
        changed = introduce(data["failure"]) or changed
    return Advice.model_validate(data) if changed else proposal


def complete_routine_steps(world, actor, proposal):
    """Normalize safe bookkeeping without another model call."""
    data = proposal.model_dump()
    selected = {
        entity_id
        for outcome in (data["success"], data.get("failure"))
        if outcome
        for operation in outcome["operations"]
        if operation["op"] == "set_state"
        for entity_id in operation["using"]
        if world["entities"].get(entity_id, {}).get("kind") == "item"
    }
    start = world["entities"][actor]["location"]
    carry = [
        entity_id
        for entity_id in selected
        if world["entities"][entity_id]["location"] == start
    ]
    if not carry:
        carry = []
    for outcome in (data["success"], data.get("failure")):
        if not outcome:
            continue
        operations = outcome["operations"]

        speaker = data.get("speaker")
        if (
            speaker
            and speaker in world["entities"]
            and speaker not in world.get("observations", {}).get(actor, {})
            and not any(op["op"] == "present" and op["subject"] == speaker for op in operations)
        ):
            operations = [{"op": "present", "subject": speaker}, *operations]

        # Sharing something every recipient already knows is a harmless model no-op.
        operations = [
            op
            for op in operations
            if not (
                op["op"] == "share_fact"
                and op["fact"] in world["facts"]
                and all(i in world["facts"][op["fact"]]["known_by"] for i in op["recipients"])
            )
        ]

        # A discovery is itself the result of looking; persist that observation even if the
        # model omitted the redundant bookkeeping operation.
        normalized = []
        observed = {op["subject"] for op in operations if op["op"] in {"observe", "present"}}
        observed.update(
            subject
            for subject, signature in world.get("observations", {}).get(actor, {}).items()
            if subject in world["entities"] and world["entities"][subject].get("state", "") == signature
        )
        for op in operations:
            if op["op"] == "discover":
                discovery = next(
                    (
                        item
                        for item in world.get("document", {}).get("discoveries", [])
                        if item["fact"] == op["fact"]
                    ),
                    None,
                )
                if discovery and discovery["at"] not in observed:
                    normalized.append({"op": "observe", "subject": discovery["at"]})
                    observed.add(discovery["at"])
            normalized.append(op)
        operations = normalized

        moves_actor = any(
            op["op"] in {"move", "move_path"} and op["entity"] == actor
            for op in operations
        )
        if not moves_actor:
            outcome["operations"] = operations
            continue
        transferred = {
            op["item"] for op in operations if op["op"] == "transfer"
        }
        implied = [
            {"op": "transfer", "item": item, "before": start, "destination": actor}
            for item in carry
            if item not in transferred
        ]
        # A selected nearby tool must be picked up before the actor leaves its place.
        pickups = [
            op
            for op in operations
            if op["op"] == "transfer" and op["before"] == start and op["destination"] == actor
        ]
        rest = [op for op in operations if op not in pickups]
        outcome["operations"] = implied + pickups + rest

    finishing = []
    for outcome in (data["success"], data.get("failure")):
        if not outcome or not any(op["op"] == "finish_session" for op in outcome["operations"]):
            continue
        thread_id = data["pressure"].get("thread")
        open_threads = [
            item["id"] for item in world.get("threads", {}).values() if not item["resolved"]
        ]
        resolved_here = [
            op["thread"]
            for op in outcome["operations"]
            if op["op"] == "resolve_thread" and op["thread"] in open_threads
        ]
        if thread_id not in open_threads and open_threads:
            thread_id = resolved_here[0] if resolved_here else sorted(open_threads)[0]
        if thread_id in open_threads and not any(
            op["op"] == "resolve_thread" and op["thread"] == thread_id
            for op in outcome["operations"]
        ):
            normalized = []
            for op in outcome["operations"]:
                if op["op"] == "finish_session":
                    normalized.append(
                        {"op": "resolve_thread", "thread": thread_id, "outcome": op["summary"]}
                    )
                normalized.append(op)
            outcome["operations"] = normalized
        finishing.append(thread_id)

    advances = [
        op
        for outcome in (data["success"], data.get("failure"))
        if outcome
        for op in outcome["operations"]
        if op["op"] == "advance_thread"
    ]
    if finishing:
        data["pressure"]["level"] = "verdict"
        data["pressure"]["thread"] = finishing[0]
        if not data["pressure"]["reason"]:
            data["pressure"]["reason"] = "Одна из веток окончательно завершает историю."
    elif advances:
        highest = max(advances, key=lambda op: op["after"])
        data["pressure"]["level"] = {1: "sign", 2: "complication", 3: "verdict"}[highest["after"]]
        data["pressure"]["thread"] = highest["thread"]
        if not data["pressure"]["reason"]:
            data["pressure"]["reason"] = "Одна из веток повышает ставки сюжетной нити."
    return Advice.model_validate(data)


def apply_operations(world, operations, actor, event_id, participants=None):
    """Validate a complete player intent on a copy, then return its atomic result."""
    w = deepcopy(world)
    entities, facts = w["entities"], w["facts"]
    w.setdefault("observations", {})
    w.setdefault("threads", {})
    w.setdefault("session", {"status": "active", "ending": None})
    authorized = set(participants or [actor])
    authorized.add(actor)
    require(
        all(
            i in entities
            and entities[i]["kind"] == "hero"
            and entities[i].get("status", "active") == "active"
            for i in authorized
        ),
        "Действовать могут только выбранные живые герои.",
    )
    require(
        len({entities[i]["location"] for i in authorized}) == 1,
        "Участники общего действия должны находиться в одной сцене.",
    )
    moves, heals, damage, consumed = set(), set(), set(), []
    for obj in operations:
        op = obj.model_dump() if isinstance(obj, BaseModel) else obj
        kind = op["op"]
        if kind == "create":
            ent = deepcopy(op["entity"])
            require(ent["id"] not in entities, "Объект с таким ID уже существует.")
            require(ent["location"] in entities, "Место нового объекта неизвестно.")
            parent = entities[ent["location"]]
            require(
                parent["kind"] == "location"
                or (ent["kind"] == "item" and parent["kind"] in {"hero", "npc"}),
                "Новый объект должен появиться в месте или у персонажа.",
            )
            require(
                not any(e["name"].casefold() == ent["name"].casefold() for e in entities.values()),
                "Объект с таким именем уже существует.",
            )
            ent.setdefault("capabilities", [])
            ent.setdefault("state", "")
            ent.setdefault("states", [])
            if ent["kind"] == "location":
                w["connections"].append([ent["location"], ent["id"]])
                ent["location"] = None
            else:
                for observed in w["observations"].values():
                    observed.pop(ent["location"], None)
            entities[ent["id"]] = ent
        elif kind in {"move", "move_path"}:
            ent = entities.get(op["entity"])
            require(
                ent and ent["kind"] in {"hero", "npc"}, "Неизвестный участник перехода."
            )
            require(ent["id"] in authorized or ent["kind"] == "npc", "Герой не выбран участником действия.")
            require(ent.get("status", "active") == "active", "Погибший герой не может перемещаться.")
            require(ent["id"] not in moves, "Для участника нужен один цельный маршрут на действие.")
            route = op["route"] if kind == "move_path" else [op["before"], op["destination"]]
            require(route[0] == ent["location"], "Маршрут начинается не там, где находится участник.")
            require(len(route) == len(set(route)), "Маршрут не должен ходить по кругу.")
            require(
                all(
                    entities.get(place, {}).get("kind") == "location"
                    for place in route
                ),
                "Маршрут содержит неизвестное место.",
            )
            require(
                all(
                    any(set(edge) == {left, right} for edge in w["connections"])
                    and route_passable(w, left, right)
                    for left, right in zip(route, route[1:])
                ),
                "Маршрут содержит неизвестный или закрытый переход.",
            )
            moves.add(ent["id"])
            ent["location"] = route[-1]
        elif kind == "transfer":
            item, dest = entities.get(op["item"]), entities.get(op["destination"])
            require(
                item and item["kind"] == "item" and dest and dest["kind"] in {"hero", "npc", "location"},
                "Неизвестный предмет или получатель.",
            )
            require(
                item["location"] == op["before"] and item["location"] != "consumed",
                "Предмет уже передан или потрачен.",
            )

            owner = entities.get(item["location"])
            require(
                owner
                and entity_place(entities, owner["id"])
                == entity_place(entities, dest["id"])
                == entities[actor]["location"],
                "Передача предмета требует находиться рядом.",
            )
            item["location"] = op["destination"]
            for observed in w["observations"].values():
                observed.pop(item["id"], None)
        elif kind == "consume":
            item = entities.get(op["item"])
            require(
                item
                and item["kind"] == "item"
                and op["owner"] in authorized
                and item["location"] == op["owner"],
                "Предмет отсутствует в инвентаре героя.",
            )
            item["location"] = "consumed"
            consumed.append(item["id"])
        elif kind in {"damage", "heal"}:
            ent = entities.get(op["entity"])
            require(
                ent and ent["kind"] == "hero" and ent["id"] in authorized,
                "Герой не выбран участником действия.",
            )
            require(ent.get("status", "active") == "active", "На погибшего героя нельзя воздействовать.")
            seen = heals if kind == "heal" else damage
            require(ent["id"] not in seen, "Эффект HP повторён в одном исходе.")
            seen.add(ent["id"])
            require(kind != "heal" or ent["hp"] < ent["max_hp"], "Герой уже здоров.")
            ent["hp"] = max(0, min(ent["max_hp"], ent["hp"] + (3 if kind == "heal" else -2)))
            if ent["hp"] == 0:
                ent["status"] = "dead"
        elif kind == "remember":
            require(op["id"] not in facts, "Факт с таким ID уже существует.")
            require(
                all(i in entities and entities[i]["kind"] in {"hero", "npc"} for i in op["known_by"]),
                "Неизвестный носитель знания.",
            )
            require(all(i in entities for i in op.get("subjects", [])), "Факт связан с неизвестным объектом.")
            facts[op["id"]] = {k: v for k, v in op.items() if k != "op"} | {
                "source": event_id,
                "resolved": False,
            }
        elif kind == "discover":
            fact = facts.get(op["fact"])
            require(fact is not None, "Открываемый факт не существует.")
            require(
                any(i in authorized for i in op["knowers"]),
                "Результат исследования должен стать известен участнику действия.",
            )
            require(
                all(i in entities and entities[i]["kind"] in {"hero", "npc"} for i in op["knowers"]),
                "Неизвестный носитель знания.",
            )
            require(
                any(d["fact"] == op["fact"] and entity_place(entities, d["at"]) == entities[actor]["location"]
                    for d in w.get("document", {}).get("discoveries", [])),
                "Этот факт нельзя открыть в текущем месте.",
            )
            require(
                any(i not in fact["known_by"] for i in op["knowers"]),
                "Этот факт уже известен указанным участникам.",
            )
            fact["known_by"] = list(dict.fromkeys(fact["known_by"] + op["knowers"]))
            fact["visibility"] = "public"
        elif kind == "share_fact":
            fact = facts.get(op["fact"])
            require(fact and op["source"] in fact["known_by"], "Источник не знает передаваемый факт.")
            participants = [op["source"], *op["recipients"]]
            require(
                all(i in entities and entities[i]["kind"] in {"hero", "npc"} for i in participants),
                "Неизвестный участник разговора.",
            )
            require(
                all(entity_place(entities, i) == entities[actor]["location"] for i in participants),
                "Передача знания требует находиться рядом.",
            )
            require(
                any(i not in fact["known_by"] for i in op["recipients"]),
                "Все получатели уже знают этот факт.",
            )
            fact["known_by"] = list(dict.fromkeys(fact["known_by"] + op["recipients"]))
        elif kind == "observe":
            subject = entities.get(op["subject"])
            require(subject is not None, "Объект наблюдения не существует.")
            require(
                entity_place(entities, subject["id"]) == entities[actor]["location"],
                "Нельзя осматривать недоступный объект.",
            )
            signature = subject.get("state", "")
            previous = w["observations"].get(actor, {}).get(subject["id"])
            require(previous != signature, "Герой уже осмотрел это; нового изменения не произошло.")
            w["observations"].setdefault(actor, {})[subject["id"]] = signature
        elif kind == "present":
            subject = entities.get(op["subject"])
            require(subject is not None, "Показываемый объект не существует.")
            require(
                entity_place(entities, subject["id"]) == entities[actor]["location"],
                "Нельзя вывести в сцену недоступный объект.",
            )
            w["observations"].setdefault(actor, {})[subject["id"]] = subject.get("state", "")
        elif kind == "react_state":
            entity = entities.get(op["entity"])
            reaction = next(
                (
                    item
                    for item in w.get("document", {}).get("reactions", [])
                    if item["id"] == op["reaction"]
                ),
                None,
            )
            declared = reaction and any(
                effect["effect"] == "set_state"
                and effect["entity"] == op["entity"]
                and effect["state"] == op["after"]
                for effect in reaction["effects"]
            )
            require(entity is not None and declared, "Обязательная реакция не объявлена сценарием.")
            require(entity.get("state", "") == op["before"], "Состояние реакции уже изменилось.")
            require(op["after"] in entity.get("states", []), "Реакция задаёт неизвестное состояние.")
            entity["state"] = op["after"]
            for observed in w["observations"].values():
                observed.pop(entity["id"], None)
        elif kind == "set_state":
            entity = entities.get(op["entity"])
            require(entity is not None, "Изменяемый объект не существует.")
            require(
                entity_place(entities, entity["id"]) == entities[actor]["location"],
                "Изменяемый объект должен находиться рядом.",
            )
            require(entity.get("state", "") == op["before"], "Состояние объекта уже изменилось.")
            require(op["after"] in entity.get("states", []), "Новое состояние не объявлено сценарием.")
            _, method = transition_for(w, op)
            require(method is not None, "Такой переход и способ не объявлены сценарием.")
            require(op["using"], "Укажите участника или предмет, которым выполнено действие.")
            require(
                all(i in entities and entity_place(entities, i) == entities[actor]["location"] for i in op["using"]),
                "Для действия указано недоступное средство.",
            )
            require(
                any(op["method"] in entities[i].get("capabilities", []) for i in op["using"]),
                "Указанное средство не обладает нужной возможностью.",
            )
            entity["state"] = op["after"]
            for observed in w["observations"].values():
                observed.pop(entity["id"], None)
        elif kind == "resolve_promise":
            fact = facts.get(op["id"])
            require(
                fact and fact["status"] in {"promise", "task"} and not fact["resolved"],
                "Нет открытого обещания или задачи.",
            )
            fact["resolved"] = True
        elif kind == "advance_thread":
            thread = w["threads"].get(op["thread"])
            require(thread and not thread["resolved"], "Нет такой открытой сюжетной нити.")
            require(
                thread["pressure"] == op["before"] and op["after"] == op["before"] + 1,
                "Давление можно повысить только на одну ступень.",
            )
            thread["pressure"] = op["after"]
        elif kind == "resolve_thread":
            thread = w["threads"].get(op["thread"])
            require(thread and not thread["resolved"], "Нет такой открытой сюжетной нити.")
            thread.update(resolved=True, outcome=op["outcome"])
        elif kind == "finish_session":
            require(w["session"]["status"] == "active", "Сессия уже завершена.")
            require(
                any(thread["resolved"] for thread in w["threads"].values()),
                "Перед финалом зафиксируйте развязку хотя бы одной сюжетной нити.",
            )
            w["session"] = {
                "status": "finished",
                "ending": op["ending"],
                "summary": op["summary"],
                "event": event_id,
            }
        else:
            raise GameError("Неизвестная операция.")
    require(not heals or any("bandage" in i for i in consumed), "Для лечения нужно потратить перевязь.")
    require(
        len(entities) <= 60 and len(facts) <= 200, "Достигнут объём короткого прототипа. Сохраните результат."
    )
    return w


def _reaction_operations(world, reaction):
    operations = []
    for effect in reaction["effects"]:
        kind = effect["effect"]
        if kind == "set_state":
            entity = world["entities"][effect["entity"]]
            if entity.get("state", "") != effect["state"]:
                operations.append(
                    {
                        "op": "react_state",
                        "reaction": reaction["id"],
                        "entity": effect["entity"],
                        "before": entity.get("state", ""),
                        "after": effect["state"],
                    }
                )
        elif kind == "resolve_fact":
            if not world["facts"][effect["fact"]]["resolved"]:
                operations.append({"op": "resolve_promise", "id": effect["fact"]})
        elif kind == "advance_thread":
            thread = world["threads"][effect["thread"]]
            if not thread["resolved"] and thread["pressure"] < 3:
                operations.append(
                    {
                        "op": "advance_thread",
                        "thread": effect["thread"],
                        "before": thread["pressure"],
                        "after": thread["pressure"] + 1,
                    }
                )
        elif kind == "resolve_thread":
            if not world["threads"][effect["thread"]]["resolved"]:
                operations.append(
                    {
                        "op": "resolve_thread",
                        "thread": effect["thread"],
                        "outcome": effect["outcome"],
                    }
                )
    return operations


def _ending_operations(world, ending):
    operations = []
    for fact_id in ending["resolve_facts"]:
        if not world["facts"][fact_id]["resolved"]:
            operations.append({"op": "resolve_promise", "id": fact_id})
    for thread_id in ending["resolve_threads"]:
        if not world["threads"][thread_id]["resolved"]:
            operations.append(
                {
                    "op": "resolve_thread",
                    "thread": thread_id,
                    "outcome": ending["summary"],
                }
            )
    operations.append(
        {
            "op": "finish_session",
            "ending": ending["ending"],
            "summary": ending["summary"],
        }
    )
    return operations


def _join_text(left, right, limit):
    combined = " ".join(part.strip() for part in (left, right) if part and part.strip())
    return combined if len(combined) <= limit else right[:limit]


def reject_forged_rule_operations(proposal):
    for outcome in (proposal.success, proposal.failure):
        if outcome and any(operation.op == "react_state" for operation in outcome.operations):
            raise GameError("Обязательные реакции добавляет приложение, а не модель.")


def complete_authored_rules(world, action, proposal):
    """Expand mandatory authored reactions and endings on every proposed branch."""
    data = proposal.model_dump()
    actor = action["actor"]
    metadata = {}
    verdicts = []
    pressure_ops = []
    participants = action.get("participants")
    for branch_name in ("success", "failure"):
        outcome = data.get(branch_name)
        if not outcome:
            continue
        operations = list(outcome["operations"])
        base_count = len(operations)
        projected = apply_operations(
            world,
            operations,
            actor,
            "preview_" + branch_name,
            participants,
        )
        fired_this_branch = []
        reaction_summaries = []
        reaction_texts = []
        halted = False
        for _ in range(100):
            candidates = [
                reaction
                for reaction in world.get("document", {}).get("reactions", [])
                if reaction["id"] not in fired_this_branch
                and (not reaction.get("once", True) or reaction["id"] not in world.get("fired_reactions", []))
                and world_conditions_match(projected, reaction["when"], reaction.get("match", "all"))
            ]
            if not candidates:
                break
            reaction = sorted(candidates, key=lambda item: (-item["priority"], item["id"]))[0]
            extra = _reaction_operations(projected, reaction)
            if extra:
                projected = apply_operations(
                    projected,
                    extra,
                    actor,
                    "reaction_" + reaction["id"],
                    participants,
                )
                operations.extend(extra)
                pressure_ops.extend(op for op in extra if op["op"] == "advance_thread")
            fired_this_branch.append(reaction["id"])
            reaction_summaries.append(reaction["summary"])
            reaction_texts.append(reaction["read_aloud"])
            halted = halted or reaction.get("halt", False)
        else:
            raise GameError("Обязательные реакции образуют цикл.")

        endings = [
            ending
            for ending in world.get("document", {}).get("endings", [])
            if world_conditions_match(projected, ending["when"], ending.get("match", "all"))
        ]
        selected_ending = None
        if endings and projected.get("session", {}).get("status", "active") == "active":
            endings.sort(key=lambda item: (-item["priority"], item["id"]))
            selected_ending = endings[0]
            if len(endings) > 1 and endings[1]["priority"] == selected_ending["priority"]:
                raise GameError("Одновременно сработали два финала одинакового приоритета.")
            extra = _ending_operations(projected, selected_ending)
            projected = apply_operations(
                projected,
                extra,
                actor,
                "ending_" + selected_ending["id"],
                participants,
            )
            operations.extend(extra)
            outcome["summary"] = selected_ending["summary"]
            outcome["read_aloud"] = selected_ending["read_aloud"]
            verdicts.append(selected_ending)
        else:
            for summary in reaction_summaries:
                outcome["summary"] = _join_text(outcome["summary"], summary, 300)
            for read_aloud in reaction_texts:
                outcome["read_aloud"] = _join_text(outcome["read_aloud"], read_aloud, 800)

        outcome["operations"] = operations
        metadata[branch_name] = {
            "reactions": fired_this_branch,
            "ending": selected_ending["id"] if selected_ending else None,
            "protected": list(range(base_count, len(operations))),
            "halt": halted,
        }

    if verdicts:
        selected = sorted(verdicts, key=lambda item: (-item["priority"], item["id"]))[0]
        data["pressure"] = {
            "level": "verdict",
            "thread": selected["pressure_thread"],
            "reason": "Выполнено авторское условие окончательного исхода.",
        }
        if data.get("check") is None:
            data["stop"] = "finished"
    elif pressure_ops:
        highest = max(pressure_ops, key=lambda operation: operation["after"])
        data["pressure"] = {
            "level": {1: "sign", 2: "complication", 3: "verdict"}[highest["after"]],
            "thread": highest["thread"],
            "reason": "Сработало обязательное последствие мира.",
        }
    if any(item["halt"] for item in metadata.values()) and not verdicts:
        data["gm_hint"] = _join_text(
            data.get("gm_hint", ""),
            "После подтверждения остановитесь и спросите игроков, как они отвечают на новое обстоятельство.",
            400,
        )
    return Advice.model_validate(data), metadata


def validate_proposal(world, action, proposal):
    actor = action["actor"]
    participants = action.get("participants") or [actor]
    require(actor in participants, "Ведущий герой должен входить в состав участников.")
    require(
        all(i in world["facts"] for i in proposal.evidence), "Предложение ссылается на неизвестные факты."
    )
    require((proposal.check is None) == (proposal.failure is None), "Для проверки нужны обе ветки исхода.")
    require(
        (proposal.check is not None) == (proposal.stop == "check"),
        "Стоп-причина check должна точно соответствовать броску.",
    )
    require(
        all(i in world["entities"] or i in world["facts"] for i in proposal.intent.targets),
        "Намерение ссылается на неизвестную цель.",
    )
    if proposal.check:
        check_actor = proposal.check.actor or actor
        require(check_actor in participants, "Проверку выполняет герой, не участвующий в действии.")
        require(
            len(proposal.check.helpers) == len(set(proposal.check.helpers))
            and check_actor not in proposal.check.helpers
            and all(i in participants for i in proposal.check.helpers),
            "Помощники должны быть разными выбранными участниками, кроме проверяемого героя.",
        )
    require(
        proposal.pressure.thread is None or proposal.pressure.thread in world.get("threads", {}),
        "Давление ссылается на неизвестную сюжетную нить.",
    )
    if proposal.pressure.level != "ordinary":
        require(proposal.pressure.thread is not None, "Для усиления давления нужна сюжетная нить.")
        levels = {1: "sign", 2: "complication", 3: "verdict"}
        all_operations = [
            op.model_dump()
            for outcome in (proposal.success, proposal.failure)
            if outcome
            for op in outcome.operations
        ]
        advances = [
            op for op in all_operations if op["op"] == "advance_thread"
        ]
        require(
            any(
                op["thread"] == proposal.pressure.thread
                and levels[op["after"]] == proposal.pressure.level
                for op in advances
            )
            or (
                proposal.pressure.level == "verdict"
                and world["threads"][proposal.pressure.thread]["pressure"] == 3
            )
            or (
                proposal.pressure.level == "verdict"
                and any(op["op"] == "finish_session" for op in all_operations)
            ),
            "Усиление давления должно менять объявленную сюжетную нить.",
        )
    if proposal.question:
        require(
            proposal.stop == "choice" and proposal.check is None and not proposal.success.operations,
            "Уточнение должно обозначать выбор и не может менять мир.",
        )
    success = apply_operations(world, proposal.success.operations, actor, action["id"], participants)
    if proposal.speaker:
        npc = success["entities"].get(proposal.speaker)
        require(
            npc and npc["kind"] == "npc" and npc["location"] == success["entities"][actor]["location"],
            "Отвечающий NPC должен находиться рядом после разрешения намерения.",
        )
    if proposal.failure:
        failure = apply_operations(world, proposal.failure.operations, actor, action["id"], participants)
    else:
        failure = None

    if proposal.check and failure:
        success_operations = [operation.model_dump() for operation in proposal.success.operations]
        failure_operations = [operation.model_dump() for operation in proposal.failure.operations]
        require(
            not success_operations or success_operations != failure_operations,
            "Бросок бессмысленен: успех и неудача одинаково меняют мир.",
        )

    branches = [(proposal.success, success), (proposal.failure, failure)]
    for outcome, result in branches:
        if not outcome:
            continue
        operations = [op.model_dump() for op in outcome.operations]
        irreversible = any(
            op["op"] == "finish_session"
            or op["op"] == "advance_thread" and op["after"] == 3
            for op in operations
        ) or any(
            world["entities"][i].get("status", "active") == "active"
            and result["entities"][i].get("status", "active") == "dead"
            for i in participants
        )
        if irreversible:
            require(
                proposal.pressure.level == "verdict",
                "Необратимый исход требует явно обозначенного приговора.",
            )
        active = [
            e for e in result["entities"].values()
            if e["kind"] == "hero" and e.get("status", "active") == "active"
        ]
        if not active:
            require(
                any(op["op"] == "finish_session" and op["ending"] in {"defeat", "tragedy"} for op in operations),
                "Гибель последнего героя должна завершить сессию.",
            )
        finished = any(op["op"] == "finish_session" for op in operations)
        require(
            not finished or proposal.stop in {"finished", "check"},
            "Финальная операция должна быть помечена как завершение истории.",
        )

    if action.get("mode") == "hint":
        return
    operations = [op.model_dump() for op in proposal.success.operations]
    operation_kinds = {op["op"] for op in operations}
    # Observation may only foreground an authored object.  Reading, testing or
    # questioning it is a separate player choice, so a clue is no longer forced
    # open merely because the model classified a broad action as observation.
    if proposal.intent.kind == "travel" and proposal.stop == "completed" and not proposal.question:
        targets = [world["entities"].get(i) for i in proposal.intent.targets]
        destinations = {e["id"] for e in targets if e and e["kind"] == "location"}
        if destinations and world["entities"][actor]["location"] not in destinations:
            require(
                success["entities"][actor]["location"] in destinations,
                "Завершённый переход должен довести героя до заявленной цели.",
            )
    if proposal.stop == "completed" and proposal.intent.kind == "acquire":
        require(
            operation_kinds & {"transfer", "create", "consume"},
            "Получение или использование предмета должно менять его состояние.",
        )
    if proposal.stop == "completed" and proposal.intent.kind == "manipulate":
        require(operations, "Воздействие на мир не может завершаться пустым описанием.")
    if proposal.stop == "finished":
        require(
            any(op["op"] == "finish_session" for op in operations),
            "Завершённая история должна содержать финальный исход.",
        )
    for outcome in (proposal.success, proposal.failure):
        if not outcome:
            continue
        for operation in outcome.operations:
            op = operation.model_dump()
            if op["op"] != "set_state":
                continue
            _, method = transition_for(world, op)
            require(method is not None, "Неизвестный способ изменения состояния.")
            declared_check = method.get("check")
            require(
                (
                    {k: declared_check[k] for k in ("stat", "difficulty")}
                    if declared_check
                    else None
                )
                == (
                    {k: getattr(proposal.check, k) for k in ("stat", "difficulty")}
                    if proposal.check
                    else None
                ),
                "Проверка не соответствует объявленному способу.",
            )


# Application selection is independent of the operator's Codex coding settings.
DEFAULT_MODEL = "gpt-5.6-luna"
WORLD_MODELS = {
    "gpt-5.6-luna": "Luna — экономная",
    "gpt-5.6-terra": "Terra — сбалансированная",
    "gpt-6-astra": "Astra — сложные задачи",
    "": "Из настроек Codex",
}


def build_scene_context(state, action):
    """Build one compact turn folder; no model transcript is used as memory."""
    entities = state["entities"]
    document = state.get("document", {})
    participants = action.get("participants") or [action["actor"]]
    lead = entities[action["actor"]]
    places = {entities[i]["location"] for i in participants}
    lowered = action["text"].casefold()

    named = {entity["id"] for entity in entities.values() if _entity_mentioned(entity, action["text"])}
    frame = next(
        (item for item in document.get("scenes", []) if item["at"] == lead["location"]),
        None,
    )
    visible = set(participants) | places
    known_references = set()
    for participant in participants:
        for entity_id in state.get("observations", {}).get(participant, {}):
            if entity_id in entities and entity_place(entities, entity_id) in places:
                visible.add(entity_id)
            else:
                known_references.add(entity_id)
    visible.update(
        entity["id"] for entity in entities.values() if entity.get("location") in participants
    )
    for left, right in state["connections"]:
        if left in places:
            known_references.add(right)
        if right in places:
            known_references.add(left)
    if frame and frame.get("obstacle"):
        visible.add(frame["obstacle"])
    for entity_id in named:
        if entity_place(entities, entity_id) in places:
            visible.add(entity_id)
        else:
            known_references.add(entity_id)

    known_facts = {}
    fact_relevance = visible | named
    for fact_id, fact in state["facts"].items():
        subjects = set(fact.get("subjects", []))
        persistent = fact["status"] in {"promise", "task"} and not fact["resolved"]
        if any(participant in fact["known_by"] for participant in participants) and (
            persistent or not subjects or bool(subjects & fact_relevance)
        ):
            known_facts[fact_id] = fact
            known_references.update(subjects)
            visible.update(
                entity_id
                for entity_id in subjects
                if entity_id in entities
                and entities[entity_id]["kind"] == "npc"
                and entity_place(entities, entity_id) in places
            )

    opportunity_priorities = {}
    if frame:
        for opportunity in frame["opportunities"]:
            if world_conditions_match(
                state, opportunity.get("when", []), opportunity.get("match", "all")
            ):
                for subject in opportunity["subjects"]:
                    opportunity_priorities[subject] = max(
                        opportunity_priorities.get(subject, -1000), opportunity["priority"]
                    )

    nearby_unseen = [
        entity
        for entity in entities.values()
        if entity["id"] not in visible
        and entity_place(entities, entity["id"]) in places
        and entity.get("status", "active") != "dead"
    ]
    discovery_sources = {item["at"] for item in document.get("discoveries", [])}
    nearby_unseen.sort(
        key=lambda entity: (
            -opportunity_priorities.get(entity["id"], -1000),
            entity["id"] not in discovery_sources,
            entity["kind"] == "npc",
            entity["name"],
        )
    )
    possible_introductions = [
        {
            "id": entity["id"],
            "name": entity["name"],
            "kind": entity["kind"],
            "description": entity["description"],
        }
        for entity in nearby_unseen[:8]
    ]

    known_entities = {
        entity_id: {
            "id": entity_id,
            "name": entities[entity_id]["name"],
            "kind": entities[entity_id]["kind"],
        }
        for entity_id in known_references - visible
        if entity_id in entities
    }

    visible_entities = {}
    for entity_id in visible:
        entity = entities.get(entity_id)
        if not entity:
            continue
        visible_entities[entity_id] = {
            key: entity.get(key)
            for key in ("id", "kind", "name", "description", "location", "state", "status", "hp", "max_hp", "stats")
            if key in entity
        }

    locally_relevant = visible | {item["id"] for item in possible_introductions}
    private_constraints = [
        {"npc": entity_id, "goal": entities[entity_id]["goal"]}
        for entity_id in locally_relevant
        if entity_id in entities and entities[entity_id]["kind"] == "npc" and entities[entity_id].get("goal")
    ]
    private_constraints.extend({"boundary": text} for text in document.get("boundaries", []))
    private_constraints.extend(
        {"memory": fact["text"]}
        for fact in state["facts"].values()
        if fact.get("source") != "author"
        and fact["visibility"] == "gm"
        and (not fact.get("subjects") or set(fact["subjects"]) & locally_relevant)
    )

    observations = {
        participant: {
            entity_id: signature
            for entity_id, signature in state.get("observations", {}).get(participant, {}).items()
            if entity_id in visible
        }
        for participant in participants
    }
    discoveries = []
    for discovery in document.get("discoveries", []):
        fact = state["facts"][discovery["fact"]]
        if any(participant in fact["known_by"] for participant in participants):
            continue
        if discovery["at"] in visible:
            discoveries.append({**discovery, "result": fact["text"]})

    providers = set(participants)
    providers.update(
        entity["id"]
        for entity in entities.values()
        if entity.get("location") in participants
        or entity["id"] in visible and entity_place(entities, entity["id"]) in places
    )
    mechanical_options = []
    transitions = []
    reachable_places = set(places)
    for entity_id in named:
        place = entity_place(entities, entity_id)
        if place and shortest_path(state, lead["location"], place, lead["id"]):
            reachable_places.add(place)
    for transition in document.get("transitions", []):
        target = entities[transition["entity"]]
        if (
            transition["entity"] not in visible
            or entity_place(entities, transition["entity"]) not in reachable_places
            or target.get("state", "") != transition["before"]
            or not world_conditions_match(
                state, transition.get("when", []), transition.get("match", "all")
            )
        ):
            continue
        transitions.append(transition)
        for method in transition["methods"]:
            using = sorted(
                entity_id
                for entity_id in providers
                if method["capability"] in entities[entity_id].get("capabilities", [])
                and entity_place(entities, entity_id) in places
            )
            if using:
                mechanical_options.append(
                    {
                        "transition": transition["id"],
                        "target": transition["entity"],
                        "result": transition["after"],
                        "method": method["capability"],
                        "using": using,
                        "check": method.get("check"),
                    }
                )

    relevant = visible | {item["id"] for item in possible_introductions}
    threads = {
        thread_id: thread
        for thread_id, thread in state.get("threads", {}).items()
        if thread["pressure"] > 0 or not thread["subjects"] or set(thread["subjects"]) & relevant
    }
    routes = {
        entity_id: path
        for entity_id, entity in entities.items()
        if entity["kind"] == "location"
        and entity_id != lead["location"]
        and (path := shortest_path(state, lead["location"], entity_id, lead["id"]))
    }
    scene_locations = {item["at"] for item in document.get("scenes", [])}
    travel_destinations = set(routes)
    travel_destinations.update(
        entity_id
        for entity_id, entity in known_entities.items()
        if entity["kind"] == "location" and entity_id != lead["location"]
    )
    travel_options = []
    for destination in sorted(travel_destinations):
        plan = travel_plan(state, lead["location"], destination, lead["id"])
        if not plan or len(plan["route"]) <= 1 and plan["reached"]:
            continue
        stop_at = plan["route"][-1]
        travel_options.append(
            {
                "destination": destination,
                "name": entities[destination]["name"],
                "safe_route": plan["route"],
                "reaches_goal": plan["reached"],
                "stops_at": stop_at,
                "blocked_text": plan["blocked"]["reason"] if plan["blocked"] else None,
                "meaningful_stop": stop_at in scene_locations or not plan["reached"],
            }
        )
    blocked_routes = []
    for route in document.get("routes", []):
        if lead["location"] not in route["between"] or world_conditions_match(
            state, route["when"], route.get("match", "all")
        ):
            continue
        destination = next(item for item in route["between"] if item != lead["location"])
        blocked_routes.append(
            {"destination": destination, "reason": route["blocked_text"]}
        )
    recent = [
        {
            "action": event["action"]["text"],
            "summary": event["summary"],
            "text": event["text"],
            "progress": event.get("progress", "meaningful"),
            "failed_roll": bool(event.get("roll") and not event["roll"]["success"]),
        }
        for event in state["events"]
        if event["action"].get("mode") != "hint"
    ][-2:]
    repeated = sum(item["action"].strip().casefold() == lowered.strip() for item in recent)
    scene_context = None
    if frame:
        opportunities = []
        observed = set().union(
            *(set(state.get("observations", {}).get(participant, {})) for participant in participants)
        )
        for opportunity in sorted(
            frame["opportunities"], key=lambda item: (-item["priority"], item["id"])
        ):
            if not world_conditions_match(
                state, opportunity.get("when", []), opportunity.get("match", "all")
            ):
                continue
            opportunities.append(
                {
                    "id": opportunity["id"],
                    "title": opportunity["title"],
                    "cue": opportunity["cue"],
                    "subjects": opportunity["subjects"],
                    "supports": opportunity["supports"],
                    "introduced": all(subject in observed for subject in opportunity["subjects"]),
                    "priority": opportunity["priority"],
                }
            )
        fresh = [item for item in opportunities if not item["introduced"]]
        stalled = bool(
            repeated
            or recent
            and (recent[-1]["progress"] == "no_progress" or recent[-1]["failed_roll"])
        )
        scene_context = {
            "id": frame["id"],
            "at": frame["at"],
            "goal": frame["goal"],
            "obstacle": frame.get("obstacle"),
            "transitions": frame["transitions"],
            "opportunities": opportunities,
            "fresh_opportunities": fresh,
            "momentum_opportunity": fresh[0] if stalled and fresh else None,
        }
    return {
        "visible_entities": visible_entities,
        "known_entities": known_entities,
        "possible_introductions": possible_introductions,
        "known_facts": known_facts,
        "private_constraints": private_constraints,
        "observations": observations,
        "available_discoveries": discoveries,
        "mechanical_options": mechanical_options,
        "threads": threads,
        "routes": routes,
        "travel_options": travel_options,
        "blocked_routes": blocked_routes,
        "scene_frame": scene_context,
        "scenario": {
            "title": document.get("title"),
            "manual_rules": document.get("rules", {}).get("manual_rules", []),
        },
        "session": state.get("session", {"status": "active", "ending": None}),
        "recent_events": recent,
        "pace": {"resolved_cards": len(state["events"]), "exact_repetitions": repeated},
        "entity_index": {
            entity_id: {"name": entities[entity_id]["name"], "kind": entities[entity_id]["kind"]}
            for entity_id in visible | set(known_entities)
            if entity_id in entities
        },
        "action": action,
    }


class FreeWorld:
    def __init__(self, directory, provider=None):
        directory = directory.resolve()
        self.store = Store(directory / "free-world.sqlite3")
        local_cli = directory / "codex-cli/node_modules/.bin" / ("codex.cmd" if os.name == "nt" else "codex")
        self.provider = provider or CodexProvider(executable=str(local_cli) if local_cli.is_file() else None)
        self.thread = None
        self.cancel = threading.Event()
        self.lock = threading.RLock()
        state = self.store.read()
        if state and state["pending"] and state["pending"]["phase"] == "planning":
            self.update_pending(
                state["pending"]["id"],
                lambda p: p.update(
                    phase="error", error="Приложение перезапущено. Повторите запрос; мир не изменён."
                ),
            )

    def view(self):
        state = self.store.read()
        projection = None
        travel_routes = {}
        if state:
            places = [e["id"] for e in state["entities"].values() if e["kind"] == "location"]
            for hero in state["entities"].values():
                if hero["kind"] != "hero" or hero.get("status", "active") != "active":
                    continue
                travel_routes[hero["id"]] = {
                    place: path
                    for place in places
                    if place != hero["location"]
                    and (path := shortest_path(state, hero["location"], place, hero["id"]))
                }
        if state and state.get("pending") and state["pending"]["phase"] == "ready":
            p = state["pending"]
            after = apply_operations(
                state,
                p["outcome"]["operations"],
                p["action"]["actor"],
                p["action"]["id"],
                p["action"].get("participants"),
            )
            hero = after["entities"][p["action"]["actor"]]
            projection = dict(
                name=hero["name"],
                location=after["entities"][hero["location"]]["name"],
                hp=hero["hp"],
                items=[
                    e["name"]
                    for e in after["entities"].values()
                    if e["kind"] == "item" and e["location"] == hero["id"]
                ],
            )
        visible_state = deepcopy(state) if state else None
        if visible_state:
            visible_state.pop("model_thread", None)
            visible_state.pop("model_thread_calls", None)
        return {
            "game": visible_state,
            "projection": projection,
            "model": self.store.get_meta("model", DEFAULT_MODEL),
            "models": WORLD_MODELS,
            "routes": travel_routes,
            "busy": bool(self.thread and self.thread.is_alive()),
        }

    def set_model(self, model):
        with self.lock:
            require(model in WORLD_MODELS, "Выберите модель из списка.")
            state = self.store.read()
            require(
                not (state and state["pending"]) and not (self.thread and self.thread.is_alive()),
                "Завершите или отмените текущий ход перед сменой модели.",
            )
            self.store.set_meta("model", model)
            return self.view()

    def update_pending(self, pid, fn):
        def change(s, db):
            require(s and s["pending"] and s["pending"]["id"] == pid, "Действие уже отменено или изменено.")
            fn(s["pending"])
            return s

        return self.store.mutate(uid(), None, change)

    def command(self, kind, data, command_id, revision):
        with self.lock:
            if self.store.seen(command_id):
                return self.view()
            run = None

            def change(s, db):
                nonlocal run
                if kind == "new":
                    require(not s or not s["pending"], "Сначала завершите или отмените действие.")
                    if s:
                        db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", ("previous-game", encode(s)))
                    from .world_documents import example_document, start_document

                    return start_document(example_document())
                if kind == "import":
                    from .world_documents import load_document, start_document

                    require(not s or not s["pending"], "Сначала завершите или отмените действие.")
                    imported = load_document(data.get("text", ""))
                    if s:
                        db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", ("previous-game", encode(s)))
                    replay = start_document(imported["document"])
                    replay["id"] = imported["id"]
                    for event in imported["events"]:
                        db.execute(
                            "INSERT INTO checkpoints(session, body) VALUES (?,?)",
                            (imported["id"], encode(replay)),
                        )
                        replay = apply_operations(
                            replay,
                            event["operations"],
                            event["action"]["actor"],
                            event["id"],
                            event["action"].get("participants"),
                        )
                        replay["events"].append(event)
                        replay["epoch"] += 1
                    return imported
                require(s is not None, "Начните короткую игру.")
                p = s["pending"]
                if kind in {"submit", "note", "travel", "direct"} and p and p.get("phase") == "error":
                    s["pending"] = p = None
                if kind in {"submit", "note", "travel", "direct"}:
                    require(
                        len(s["events"]) < 200,
                        "Сессия достигла 200 записей. Скачайте журнал перед новой сессией.",
                    )
                    require(
                        s.get("session", {}).get("status", "active") == "active",
                        "Эта история уже завершена. Начните новую сессию.",
                    )
                if kind == "direct":
                    require(not p, "Сначала завершите или отмените карточку.")
                    text = data.get("text", "")
                    require(
                        isinstance(text, str) and len(text.strip()) <= 600,
                        "Указание ведущего: до 600 символов.",
                    )
                    s["director_note"] = text.strip()
                elif kind == "travel":
                    require(not p, "Сначала завершите или отмените карточку.")
                    actor, destination = data.get("actor"), data.get("destination")
                    require(
                        isinstance(actor, str)
                        and actor in s["entities"]
                        and s["entities"][actor]["kind"] == "hero",
                        "Выберите героя.",
                    )
                    require(s["entities"][actor].get("status", "active") == "active", "Погибший герой не действует.")
                    require(isinstance(destination, str), "Выберите место.")
                    hero = s["entities"][actor]
                    route = shortest_path(s, hero["location"], destination, actor)
                    require(route and len(route) > 1, "До выбранного места нет известного безопасного пути.")
                    action_id = uid()
                    ops = [
                        {
                            "op": "move_path",
                            "entity": actor,
                            "route": route,
                        }
                    ]
                    summary = f"{hero['name']}: {s['entities'][hero['location']]['name']} → {s['entities'][destination]['name']}"
                    action = dict(
                        id=action_id, actor=actor, participants=[actor], text=summary, mode="manual"
                    )
                    local_plan = Advice.model_validate(
                        dict(
                            summary=summary,
                            intent={"kind": "travel", "goal": summary, "targets": [destination]},
                            stop="completed",
                            evidence=[],
                            gm_hint="Переход по известному пути. Проверьте обязательные последствия и подтвердите.",
                            check=None,
                            question=None,
                            speaker=None,
                            success={"summary": summary, "operations": ops, "read_aloud": ""},
                            failure=None,
                        )
                    )
                    local_plan, rule_options = complete_authored_rules(s, action, local_plan)
                    validate_proposal(s, action, local_plan)
                    outcome = local_plan.success.model_dump()
                    rule_meta = rule_options.get("success", {})
                    s["pending"] = dict(
                        id=uid(),
                        phase="ready",
                        action=action,
                        epoch=s["epoch"],
                        attempts=0,
                        pipeline="local",
                        outcome=outcome,
                        text=outcome["read_aloud"],
                        proposal=local_plan.model_dump(),
                        rule_options=rule_options,
                        reactions=rule_meta.get("reactions", []),
                        ending=rule_meta.get("ending"),
                        protected_operations=rule_meta.get("protected", []),
                    )
                elif kind == "submit":
                    require(not p, "Завершите или отмените текущее действие.")
                    actor = data.get("actor") or next(
                        e["id"]
                        for e in s["entities"].values()
                        if e["kind"] == "hero" and e.get("status", "active") == "active"
                    )
                    text = data.get("text", "")
                    request_mode = data.get("mode", "action")
                    require(
                        isinstance(request_mode, str) and request_mode in {"action", "hint"},
                        "Выберите заявку игрока или вопрос ведущего.",
                    )
                    require(
                        isinstance(actor, str)
                        and actor in s["entities"]
                        and s["entities"][actor]["kind"] == "hero",
                        "Выберите героя.",
                    )
                    participants = data.get("participants") or [actor]
                    require(
                        isinstance(participants, list)
                        and 1 <= len(participants) <= 4
                        and len(participants) == len(set(participants))
                        and actor in participants
                        and all(
                            isinstance(i, str)
                            and i in s["entities"]
                            and s["entities"][i]["kind"] == "hero"
                            and s["entities"][i].get("status", "active") == "active"
                            for i in participants
                        ),
                        "Выберите от одного до четырёх живых участников.",
                    )
                    require(
                        len({s["entities"][i]["location"] for i in participants}) == 1,
                        "Совместное действие доступно героям в одной сцене.",
                    )
                    require(
                        isinstance(text, str) and 0 < len(text.strip()) <= 2000,
                        "Введите действие до 2000 символов.",
                    )
                    s["pending"] = {
                        "id": uid(),
                        "phase": "planning",
                        "action": {
                            "id": uid(),
                            "actor": actor,
                            "participants": participants,
                            "text": text.strip(),
                            "mode": request_mode,
                        },
                        "pipeline": "intent-engine",
                        "epoch": s["epoch"],
                        "attempts": 1,
                        "model": self.store.get_meta("model", DEFAULT_MODEL)
                        or getattr(self.provider, "selected_model", lambda: "")(),
                    }
                    run = "plan"
                elif kind == "cancel":
                    require(p is not None, "Нет текущего действия.")
                    s["pending"] = None
                elif kind == "retry":
                    require(p and p["phase"] == "error", "Повтор доступен после ошибки.")
                    require(p["attempts"] < 3, "Три попытки исчерпаны. Отмените и уточните действие.")
                    for key in (
                        "proposal", "outcome", "text", "roll", "rule_options", "reactions",
                        "ending", "protected_operations", "edited",
                    ):
                        p.pop(key, None)
                    p.update(id=uid(), phase="planning", error=None, attempts=p["attempts"] + 1)
                    run = "plan"
                elif kind == "redirect":
                    require(
                        p and p["phase"] in {"ready", "check", "question", "error"},
                        "Сначала дождитесь карточки, которую нужно поправить.",
                    )
                    text = data.get("text", "")
                    require(
                        isinstance(text, str) and 0 < len(text.strip()) <= 600,
                        "Исправление ведущего: от 1 до 600 символов.",
                    )
                    require(p["attempts"] < 3, "Три варианта исчерпаны. Отклоните карточку и уточните заявку.")
                    s["director_note"] = text.strip()
                    for key in (
                        "proposal", "outcome", "text", "roll", "rule_options", "reactions",
                        "ending", "protected_operations", "edited",
                    ):
                        p.pop(key, None)
                    p.update(id=uid(), phase="planning", error=None, attempts=p["attempts"] + 1)
                    run = "plan"
                elif kind == "roll":
                    require(p and p["phase"] == "check", "Сначала дождитесь проверки.")
                    value = data.get("value")
                    require(
                        value is None or type(value) is int and 1 <= value <= 20, "Бросок: число от 1 до 20."
                    )
                    value = value or secrets.randbelow(20) + 1
                    check = p["proposal"]["check"]
                    check_actor = check.get("actor") or p["action"]["actor"]
                    stat_bonus = s["entities"][check_actor]["stats"][check["stat"]]
                    assist_bonus = min(2, len(check.get("helpers", [])))
                    bonus = stat_bonus + assist_bonus
                    dc = {"easy": 10, "standard": 13, "hard": 16}[check["difficulty"]]
                    p["roll"] = dict(value=value, bonus=bonus, dc=dc, success=value + bonus >= dc)
                    branch = "success" if p["roll"]["success"] else "failure"
                    p["outcome"] = p["proposal"][branch]
                    rule_meta = p.get("rule_options", {}).get(branch, {})
                    p["reactions"] = rule_meta.get("reactions", [])
                    p["ending"] = rule_meta.get("ending")
                    p["protected_operations"] = rule_meta.get("protected", [])
                    p.update(phase="ready", text=p["outcome"]["read_aloud"])
                elif kind == "edit":
                    require(p and p["phase"] == "ready", "Сначала дождитесь готовой карточки.")
                    text, summary = data.get("text"), data.get("summary")
                    require(isinstance(text, str) and len(text) <= 1800, "Текст: до 1800 символов.")
                    require(
                        isinstance(summary, str) and 0 < len(summary.strip()) <= 800,
                        "Итог: от 1 до 800 символов.",
                    )
                    # Removing an effect may invalidate dependants; validate the entire remaining set.
                    keep = data.get("keep", list(range(len(p["outcome"]["operations"]))))
                    require(
                        isinstance(keep, list)
                        and all(type(i) is int and 0 <= i < len(p["outcome"]["operations"]) for i in keep)
                        and len(keep) == len(set(keep)),
                        "Неверный список изменений.",
                    )
                    require(keep == sorted(keep), "Порядок последствий нельзя менять.")
                    protected = set(p.get("protected_operations", []))
                    require(
                        protected <= set(keep),
                        "Обязательные последствия сценария нельзя удалить из карточки.",
                    )
                    require(
                        not protected or keep == list(range(len(p["outcome"]["operations"]))),
                        "Карточку с обязательным последствием нельзя разбирать по частям; отклоните её целиком.",
                    )
                    ops = [op for i, op in enumerate(p["outcome"]["operations"]) if i in keep]
                    apply_operations(
                        s, ops, p["action"]["actor"], p["action"]["id"], p["action"].get("participants")
                    )
                    p["outcome"].update(summary=summary.strip(), operations=ops)
                    p.update(
                        text=text.strip(),
                        edited=True,
                        protected_operations=[
                            new_index
                            for new_index, old_index in enumerate(keep)
                            if old_index in protected
                        ],
                    )
                elif kind == "note":
                    require(not p, "Сначала завершите или отмените карточку.")
                    text = data.get("text", "")
                    require(
                        isinstance(text, str) and 0 < len(text.strip()) <= 600,
                        "Запись ведущего: от 1 до 600 символов.",
                    )
                    visibility = data.get("visibility", "gm")
                    require(
                        isinstance(visibility, str) and visibility in {"gm", "public"},
                        "Неверная видимость записи.",
                    )
                    actor = data.get("actor") or next(
                        e["id"]
                        for e in s["entities"].values()
                        if e["kind"] == "hero" and e.get("status", "active") == "active"
                    )
                    require(
                        actor in s["entities"] and s["entities"][actor]["kind"] == "hero", "Выберите героя."
                    )
                    require(s["entities"][actor].get("status", "active") == "active", "Погибший герой не действует.")
                    action_id = uid()
                    before = deepcopy(s)
                    db.execute(
                        "INSERT INTO checkpoints(session, body) VALUES (?,?)", (s["id"], encode(before))
                    )
                    ops = [
                        {
                            "op": "remember",
                            "id": "note_" + uid(),
                            "text": text.strip(),
                            "status": "fact",
                            "visibility": visibility,
                            "known_by": [actor] if visibility == "public" else [],
                            "subjects": [s["entities"][actor]["location"]],
                        }
                    ]
                    result = apply_operations(s, ops, actor, action_id, [actor])
                    result["events"].append(
                        dict(
                            id=action_id,
                            action=dict(
                                id=action_id,
                                actor=actor,
                                participants=[actor],
                                text="Решение ведущего",
                                mode="manual",
                            ),
                            summary=text.strip(),
                            text="",
                            operations=ops,
                            roll=None,
                            check=None,
                            gm_hint="",
                            edited=False,
                            progress="meaningful",
                            pressure={"level": "ordinary", "thread": None, "reason": ""},
                            reactions=[],
                            ending=None,
                            protected_operations=[],
                        )
                    )
                    result["epoch"] += 1
                    return result
                elif kind == "accept":
                    require(
                        p and p["phase"] == "ready" and p["epoch"] == s["epoch"],
                        "Нет актуального готового исхода.",
                    )
                    before = deepcopy(s)
                    before["pending"] = None
                    db.execute(
                        "INSERT INTO checkpoints(session, body) VALUES (?,?)", (s["id"], encode(before))
                    )
                    result = apply_operations(
                        s,
                        p["outcome"]["operations"],
                        p["action"]["actor"],
                        p["action"]["id"],
                        p["action"].get("participants"),
                    )
                    once_reactions = {
                        reaction["id"]
                        for reaction in s.get("document", {}).get("reactions", [])
                        if reaction.get("once", True)
                    }
                    result["fired_reactions"] = list(
                        dict.fromkeys(
                            s.get("fired_reactions", [])
                            + [item for item in p.get("reactions", []) if item in once_reactions]
                        )
                    )
                    result["events"].append(
                        {
                            "id": p["action"]["id"],
                            "action": p["action"],
                            "summary": p["outcome"]["summary"],
                            "text": p["text"],
                            "roll": p.get("roll"),
                            "operations": p["outcome"]["operations"],
                            "check": p.get("proposal", {}).get("check"),
                            "gm_hint": p.get("proposal", {}).get("gm_hint", ""),
                            "edited": p.get("edited", False),
                            "progress": p.get("proposal", {}).get("progress", "meaningful"),
                            "pressure": p.get("proposal", {}).get(
                                "pressure", {"level": "ordinary", "thread": None, "reason": ""}
                            ),
                            "reactions": p.get("reactions", []),
                            "ending": p.get("ending"),
                            "protected_operations": p.get("protected_operations", []),
                        }
                    )
                    if p["action"].get("mode") != "hint":
                        result["director_note"] = ""
                    result.update(pending=None, epoch=s["epoch"] + 1)
                    return result
                elif kind == "undo":
                    require(not p, "Сначала завершите или отмените действие.")
                    row = db.execute(
                        "SELECT id,body FROM checkpoints WHERE session=? ORDER BY id DESC LIMIT 1", (s["id"],)
                    ).fetchone()
                    require(row is not None, "Нет принятого хода для отмены.")
                    import json

                    restored = json.loads(row["body"])
                    restored.update(calls=s["calls"], epoch=s["epoch"] + 1)
                    restored.pop("model_thread", None)
                    restored.pop("model_thread_calls", None)
                    db.execute("DELETE FROM checkpoints WHERE id=?", (row["id"],))
                    return restored
                else:
                    raise GameError("Неизвестная команда свободного мира.")
                return s

            # A cancelled process must finish before a new model request starts.
            if run is None and kind in {"new", "import", "submit", "retry", "roll", "note", "travel", "direct"}:
                require(
                    not self.thread or not self.thread.is_alive(), "Предыдущий запрос ещё останавливается."
                )
            result = self.store.mutate(command_id, revision, change)
            if kind == "cancel":
                self.cancel.set()
            if run:
                self.cancel = threading.Event()
                self.thread = threading.Thread(target=self.work, args=(result, run, self.cancel), daemon=True)
                self.thread.start()
            return self.view()

    def call(self, state, stage, instruction, payload, schema, cancel):
        pid, call_id = state["pending"]["id"], uid()

        def begin(s, db):
            require(s["pending"] and s["pending"]["id"] == pid, "Действие уже изменено.")
            count = sum(c["action"] == state["pending"]["action"]["id"] for c in s["calls"])
            require(count < 5, "Достигнут предел пяти вызовов на действие. Отмените или уточните заявку.")
            s["calls"].append(
                dict(
                    id=call_id,
                    action=state["pending"]["action"]["id"],
                    stage=stage,
                    prompt_version="session-director",
                    status="started",
                    created=time.time(),
                )
            )
            return s

        self.store.mutate(uid(), None, begin)
        details = {"status": "error"}
        try:
            result, details = self.provider.structured(
                instruction,
                payload,
                schema,
                cancel,
                state["pending"].get("model", self.store.get_meta("model", "")),
            )
            details["status"] = "completed"
            return result
        finally:

            def finish(s, db):
                if s and s["id"] == state["id"]:
                    for c in s["calls"]:
                        if c["id"] == call_id:
                            c.update(details)
                return s

            self.store.mutate(uid(), None, finish)

    def work(self, state, mode, cancel):
        pid = state["pending"]["id"]
        try:
            p = state["pending"]
            context = build_scene_context(state, p["action"])
            context["director_note"] = state.get("director_note", "")
            card = self.call(state, "direct", DIRECTOR, context, DirectorCard, cancel)

            def prepare(candidate):
                compiled = (
                    complete_routine_steps(state, p["action"]["actor"], candidate)
                    if isinstance(candidate, Advice)
                else compile_director_card(state, p["action"], candidate, context)
                )
                reject_forged_rule_operations(compiled)
                compiled, authored = complete_authored_rules(state, p["action"], compiled)
                validate_proposal(state, p["action"], compiled)
                return compiled, authored

            proposal, rule_options = prepare(card)
            if p["action"].get("mode") == "hint":
                require(
                    proposal.stop == "advice"
                    and not proposal.check
                    and not proposal.success.operations,
                    "Совет ведущему не должен менять мир.",
                )
            require(not cancel.is_set(), "Запрос отменён.")
            phase = "question" if proposal.question else "check" if proposal.check else "ready"
            values = dict(proposal=proposal.model_dump(), phase=phase, rule_options=rule_options)
            if phase == "ready":
                chosen = rule_options.get("success", {})
                values.update(
                    outcome=proposal.success.model_dump(),
                    text=proposal.success.read_aloud,
                    reactions=chosen.get("reactions", []),
                    ending=chosen.get("ending"),
                    protected_operations=chosen.get("protected", []),
                )
            self.update_pending(pid, lambda pending: pending.update(values))
        except Exception as exc:
            message = (
                str(exc) if isinstance(exc, GameError) else "Не удалось обработать запрос. Мир не изменён."
            )
            try:
                self.update_pending(pid, lambda p: p.update(phase="error", error=message))
            except GameError:
                pass

    def close(self):
        self.cancel.set()
        self.provider.close()
        if self.thread:
            self.thread.join(timeout=12)
