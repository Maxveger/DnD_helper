"""Interpreter for declarative@1; document data can only invoke these operations."""

from copy import deepcopy

from .adventures import valid_value
from .rules import require


class DeclarativeRules:
    id = "declarative@1"

    def __init__(self, definition):
        self.d = definition

    def initial_world(self, party_size):
        return {
            "variables": {k: v["initial"] for k, v in self.d["variables"].items()},
            "attempts": [],
            "ending": None,
            "combat": False,
            "turn": None,
        }

    def validate(self, state):
        require(state["scene"] in self.d["scenes"], "Неизвестная сцена.")
        require(set(state["world"]["variables"]) == set(self.d["variables"]), "Неверный набор переменных.")
        for k, value in state["world"]["variables"].items():
            require(valid_value(self.d["variables"][k], value), "Неверное значение переменной: " + k)
        require(set(state["clues"]) <= set(self.d["clues"]), "Неизвестная улика.")
        for c in state["characters"].values():
            require(type(c["hp"]) is int and 0 <= c["hp"] <= c["max_hp"], "Недопустимое здоровье.")
            require(set(c["items"]) <= set(self.d["items"].values()), "Неизвестный предмет.")

    def matches(self, state, conditions, actor=None, target=None):
        for c in conditions:
            who = target if c["who"] == "target" else actor
            kind = c["kind"]
            if kind == "variable":
                actual = state["world"]["variables"][c["ref"]]
            elif kind == "clue":
                actual = c["ref"] in state["clues"]
            elif kind == "item":
                if who not in state["characters"]:
                    return False
                actual = self.d["items"][c["ref"]] in state["characters"][who]["items"]
            else:
                if who not in state["characters"]:
                    return False
                actual = state["characters"][who]["hp"]
            expected, op = c["value"], c["op"]
            if op == "eq" and actual != expected or op == "ne" and actual == expected:
                return False
            if op == "gte" and actual < expected or op == "lte" and actual > expected:
                return False
        return True

    def exits(self, state):
        return [e["to"] for e in self.d["scenes"][state["scene"]]["exits"] if self.matches(state, e["when"])]

    def prepare(self, state, action):
        actor, intent = action["actor"], action["intent"]
        require(actor in state["characters"], "Выберите персонажа.")
        require(not state["world"]["ending"], "Финал достигнут. Завершите приключение или отмените шаг.")
        p = {
            "action": deepcopy(action),
            "title": "Решение ведущего",
            "phase": "clarify",
            "check": None,
            "roll": None,
            "text": "В документе нет подготовленного решения для этого действия.",
            "prompt": "Уточните намерение, выберите действие или задайте исход вручную.",
            "effects": [],
            "changes": [],
            "note": "Исход из документа приключения.",
            "in_combat": False,
        }
        if intent == "unknown":
            return p
        require(intent in self.d["actions"], "Неизвестное действие.")
        a, c = self.d["actions"][intent], state["characters"][actor]
        require(c["hp"] > 0, "Персонаж выбыл. Нужна помощь или решение ведущего.")
        require(state["scene"] in a["scenes"], "Это действие доступно в другой локации.")
        require(
            not a["once"] or intent not in state["world"]["attempts"],
            "Это действие уже выполнено. Выберите другой подход.",
        )
        target = action.get("target")
        if a["target"] in {"none", "self"}:
            target = actor if a["target"] == "self" else None
        elif a["target"] == "any":
            target = target or actor
        if a["target"] != "none":
            require(target in state["characters"], "Выберите цель действия.")
            require(a["target"] != "ally" or target != actor, "Выберите другого персонажа.")
        require(self.matches(state, a["requires"], actor, target), a["blocked"])
        p["action"]["target"] = target
        p.update(title=a["name"], phase="ready")
        if a["check"]:
            check = a["check"]
            bonus = c["stats"][check["stat"]] + check["bonus"]
            dice = self.d["rules"]["dice"]
            p.update(
                phase="check",
                check={
                    "stat": check["stat"],
                    "label": self.d["rules"]["attributes"][check["stat"]],
                    "dc": check["dc"],
                    "bonus": bonus,
                    "dice": dice,
                },
                text=f"{c['name']}, брось d{dice}. Бонус {bonus:+d}.",
                success=deepcopy(a["success"]),
                failure=deepcopy(a["failure"]),
                note="При неудаче: " + a["failure"]["text"],
            )
        else:
            self.outcome(p, a["success"])
        return p

    def outcome(self, p, outcome):
        p.update(
            text=outcome["text"],
            prompt=outcome["prompt"],
            effects=deepcopy(outcome["effects"]),
            changes=[self.effect_label(e) for e in outcome["effects"]],
        )

    def effect_label(self, e):
        op = e["op"]
        if op == "reveal":
            return "Открыть: " + self.d["clues"][e["ref"]]
        if op in {"give", "take"}:
            return ("Выдать: " if op == "give" else "Потратить: ") + self.d["items"][e["ref"]]
        if op == "hp":
            return f"Здоровье ({e['who']}): {e['value']:+d}, в пределах максимума"
        if op == "move":
            return "Перейти: " + self.d["scenes"][e["ref"]]["name"]
        if op == "end":
            return "Финал: " + self.d["endings"][e["ref"]]["name"]
        return f"{e['ref']}: " + (f"{e['value']:+d}" if op == "increment" else str(e["value"]))

    def roll(self, plan, die, source):
        dice = self.d["rules"]["dice"]
        require(type(die) is int and 1 <= die <= dice, f"Нужно число от 1 до {dice}.")
        p = deepcopy(plan)
        total = die + p["check"]["bonus"]
        success = total >= p["check"]["dc"]
        p.update(phase="ready", roll={"die": die, "total": total, "success": success, "source": source})
        self.outcome(p, p["success" if success else "failure"])
        return p

    def apply(self, state, plan, roll_die):
        actor, target = plan["action"]["actor"], plan["action"].get("target")
        extra = []
        for e in plan["effects"]:
            op = e["op"]
            who = target if e.get("who") == "target" else actor
            w = state["world"]["variables"]
            if op == "set":
                w[e["ref"]] = e["value"]
            elif op == "increment":
                definition = self.d["variables"][e["ref"]]
                w[e["ref"]] = min(definition["maximum"], max(definition["minimum"], w[e["ref"]] + e["value"]))
            elif op == "reveal":
                if e["ref"] not in state["clues"]:
                    state["clues"].append(e["ref"])
            elif op in {"give", "take"}:
                items = state["characters"][who]["items"]
                name = self.d["items"][e["ref"]]
                if op == "give" and name not in items:
                    items.append(name)
                elif op == "take":
                    require(name in items, f"Нельзя потратить отсутствующий предмет: {name}.")
                    items.remove(name)
            elif op == "hp":
                c = state["characters"][who]
                c["hp"] = min(c["max_hp"], max(0, c["hp"] + e["value"]))
            elif op == "move":
                state["scene"] = e["ref"]
                for c in state["characters"].values():
                    c["location"] = e["ref"]
                extra.append(self.d["scenes"][e["ref"]]["text"])
            elif op == "end":
                state["world"]["ending"] = e["ref"]
                extra.append(self.d["endings"][e["ref"]]["text"])
        intent = plan["action"]["intent"]
        if self.d["actions"][intent]["once"] and intent not in state["world"]["attempts"]:
            state["world"]["attempts"].append(intent)
        return extra

    def manual(self, state, hp):
        for actor, value in hp.items():
            state["characters"][actor]["hp"] = value

    def presentation(self, state):
        return {
            "quick_actions": [
                k
                for k, a in self.d["actions"].items()
                if state["scene"] in a["scenes"] and not (a["once"] and k in state["world"]["attempts"])
            ],
            "exits": self.exits(state),
            "objectives": [
                {"text": o["text"], "done": self.matches(state, o["when"])} for o in self.d["objectives"]
            ],
            "can_finish": bool(state["world"]["ending"]),
            "ending_title": self.d["endings"]
            .get(state["world"]["ending"], {})
            .get("name", "Приключение завершено"),
        }
