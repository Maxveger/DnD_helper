"""Deterministic rules for the prepared adventure. No network or persistence here."""

from copy import deepcopy
from .content import ATTRIBUTES, INTENTS


class GameError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise GameError(message)


class SimpleRules:
    id = "simple-fantasy@0.1"

    def initial_world(self, party_size):
        guard_hp = 4 if party_size == 1 else 6
        return {
            "filter_clean": False,
            "box_open": False,
            "core": "box",
            "pump_on": False,
            "guard_hp": guard_hp,
            "guard_max_hp": guard_hp,
            "guard_disabled": False,
            "combat": False,
            "round": 0,
            "turn": None,
            "acted": [],
            "last_attacker": None,
            "help_for": None,
            "attempts": [],
        }

    def validate(self, state):
        for c in state["characters"].values():
            require(type(c["hp"]) is int and 0 <= c["hp"] <= c["max_hp"], "Недопустимое здоровье.")
        require(
            0 <= state["world"]["guard_hp"] <= state["world"]["guard_max_hp"], "Недопустимое здоровье стража."
        )
        holders = [k for k, c in state["characters"].items() if "Сердечник" in c["items"]]
        expected = [state["world"]["core"]] if state["world"]["core"] in state["characters"] else []
        require(holders == expected, "Нарушено местонахождение сердечника.")

    def manual(self, state, hp):
        if state["world"]["combat"]:
            self.end_combat(state)
        for actor, value in hp.items():
            state["characters"][actor]["hp"] = value

    def prepare(self, state, action):
        actor = action["actor"]
        require(actor in state["characters"], "Персонаж не участвует в этой игре.")
        c = state["characters"][actor]
        w = state["world"]
        intent = action["intent"]
        require(intent in INTENTS, "Неизвестное действие.")
        require(c["hp"] > 0, "Персонаж выбыл. Союзник может помочь перевязью.")
        if w["combat"]:
            require(w["turn"] == actor, "Сейчас ход другого персонажа.")
            require(
                intent in {"attack", "help", "heal", "retreat", "promise", "unknown"},
                "Сначала завершите текущий ход боя.",
            )
        p = {
            "action": deepcopy(action),
            "title": INTENTS[intent],
            "phase": "ready",
            "check": None,
            "roll": None,
            "text": "",
            "prompt": "Что делаете дальше?",
            "effects": [],
            "changes": [],
            "note": "Подготовленный исход; модель не меняет механику.",
            "in_combat": w["combat"],
        }

        def ready(text, effects=(), changes=()):
            p.update(text=text, effects=list(effects), changes=list(changes))
            return p

        def check(stat, dc, success, failure, effects=(), failed_effects=(), changes=(), failed_changes=()):
            bonus = c["stats"][stat] + (2 if w["help_for"] == actor else 0)
            p.update(
                phase="check",
                check={"stat": stat, "label": ATTRIBUTES[stat], "dc": dc, "bonus": bonus},
                text=f"{c['name']}, брось d20. Приложение прибавит бонус {bonus:+d}.",
                success=success,
                failure=failure,
                success_effects=list(effects),
                failure_effects=list(failed_effects),
                success_changes=list(changes),
                failure_changes=list(failed_changes),
            )
            return p

        if intent in {"inspect", "read", "clean", "install", "start_pump"}:
            require(state["scene"] == "reception", "Для этого вернитесь в приёмную.")
        if intent in {"talk", "promise", "persuade", "pick", "force", "attack", "take"}:
            require(state["scene"] == "workshop", "Для этого перейдите в мастерскую.")
        if intent == "inspect":
            require(
                "inspect" not in w["attempts"], "Беглый осмотр уже был. Прочитайте табличку или спросите Аду."
            )
            return check(
                "attention",
                10,
                "Крепления целы. Сердечник сняли инструментом, а не вырвали.",
                "При беглом осмотре причина неясна. Рядом есть табличка обслуживания, а за дверью кто-то работает.",
                [("clue", "careful")],
                changes=["Открыть улику: деталь сняли аккуратно"],
            )
        if intent == "read":
            return ready(
                "На табличке написано: «Перед запуском очистить фильтр. Сердечник устанавливать только после обслуживания».",
                [("clue", "manual")],
                ["Добавить инструкцию в открытия"],
            )
        if intent == "clean":
            require(not w["pump_on"], "Насос уже работает. Приключение можно завершить.")
            if w["filter_clean"]:
                return ready("Фильтр уже чист. Осталось установить сердечник и запустить насос.")
            return ready(
                "Ты открываешь сервисную крышку и вынимаешь комок мокрых листьев. Теперь воде ничто не мешает.",
                [("set", "filter_clean", True)],
                ["Фильтр очищен"],
            )
        if intent == "talk":
            return ready(
                "Ада вытирает руки: «Насос перегревался. Я сняла сердечник, пока он не сломался. Надо прочистить фильтр».",
                [("clue", "ada"), ("clue", "manual")],
                ["Узнать причину остановки насоса"],
            )
        if intent == "promise":
            return ready(
                "«Тогда мы заодно», — Ада улыбается, отключает стража и открывает ящик. Внутри лежит целый медный сердечник.",
                [
                    ("set", "box_open", True),
                    ("set", "guard_disabled", True),
                    ("end_combat",),
                    ("clue", "ada"),
                ],
                ["Ящик открыт", "Страж отключён; бой завершён"],
            )
        if intent == "persuade":
            require(
                "persuade" not in w["attempts"],
                "Попытка уже была. Объясните Аде, что сначала очистите фильтр.",
            )
            return check(
                "charm",
                10,
                "Ада кивает и открывает ящик: «Хорошо. Только сначала фильтр, обещаете?»",
                "«Сначала проверьте фильтр», — просит Ада. Она готова помочь, если вы согласитесь на безопасный ремонт.",
                [("set", "box_open", True), ("set", "guard_disabled", True)],
                changes=["Ящик открыт, страж отключён"],
            )
        if intent == "pick":
            require(not w["box_open"], "Ящик уже открыт.")
            require("pick" not in w["attempts"], "Эта попытка уже была. Нужен другой подход.")
            p = check(
                "agility",
                13,
                "Замок тихо щёлкает. В ящике лежит медный сердечник.",
                "Замок звякает. Страж поворачивается к тебе — придётся действовать!",
                [("set", "box_open", True)],
                [("begin_combat",)],
                ["Ящик открыт"],
                ["Начнётся бой со стражем"],
            )
            p["note"] = "Риск до броска: при неудаче страж начнёт бой. Игрок должен знать об этом."
            return p
        if intent == "force":
            require(not w["box_open"], "Ящик уже открыт.")
            require(not w["guard_disabled"], "Страж отключён. Ада может открыть ящик — поговорите с ней.")
            return ready(
                "Ты берёшься за крышку. Медный страж оживает и преграждает путь к ящику.",
                [("begin_combat",)],
                ["Начнётся бой; предупреди игрока перед продолжением"],
            )
        if intent == "attack":
            require(w["guard_hp"] > 0 and not w["guard_disabled"], "Страж уже отключён.")
            if not w["combat"]:
                return ready(
                    "Страж поднимает медные лапы и готовится защищать ящик. Начинается бой.",
                    [("begin_combat",)],
                    ["Начнётся бой; затем первый игрок сможет атаковать"],
                )
            return check(
                c["attack_stat"],
                10,
                "Твой удар попадает в сочленение медного стража. Механизм пошатывается.",
                "Страж поворачивается, и удар проходит мимо.",
                [("guard_damage", 2, actor)],
                changes=["Страж получит 2 урона"],
            )
        if intent == "help":
            target = action.get("target")
            require(target in state["characters"] and target != actor, "Выберите другого персонажа.")
            require(state["characters"][target]["hp"] > 0, "Выбывшему союзнику нужна перевязь.")
            require(
                state["characters"][target]["location"] == c["location"],
                "Союзник находится в другой комнате.",
            )
            return ready(
                f"Ты помогаешь: следующая подходящая проверка {state['characters'][target]['name']} получит +2.",
                [("set", "help_for", target)],
                ["Помощь: +2 к следующей проверке"],
            )
        if intent == "heal":
            target = action.get("target") or actor
            require(target in state["characters"], "Выберите участника игры.")
            require(
                state["characters"][target]["location"] == c["location"],
                "Персонаж находится в другой комнате.",
            )
            require("Перевязь" in c["items"], "Перевязь уже потрачена.")
            require(
                state["characters"][target]["hp"] < state["characters"][target]["max_hp"],
                "Здоровье уже полное.",
            )
            return ready(
                "Ты накладываешь перевязь. Боль отступает — можно продолжать.",
                [("heal", target, 3), ("consume", actor, "Перевязь")],
                ["Восстановить до 3 HP", "Потратить перевязь"],
            )
        if intent == "retreat":
            require(w["combat"], "Сейчас боя нет. Ведущий может сменить сцену.")
            return ready(
                "Ты отступаешь в приёмную. Страж остаётся возле ящика.",
                [("retreat", actor)],
                ["Персонаж покинет бой"],
            )
        if intent == "take":
            require(w["box_open"], "Сначала нужно открыть ящик.")
            require(w["core"] == "box", "Сердечник уже забрали.")
            return ready(
                "Ты берёшь медный сердечник. Он цел и подходит к пустому гнезду насоса.",
                [("core", actor)],
                [f"Сердечник получит {c['name']}"],
            )
        if intent == "install":
            require(w["core"] == actor, "Сердечник должен быть у действующего персонажа.")
            return ready(
                "Ты вставляешь сердечник в круглое гнездо. Крепления встают на место.",
                [("core", "pump")],
                ["Сердечник установлен в насос"],
            )
        if intent == "start_pump":
            require(w["core"] == "pump", "Сначала установите сердечник.")
            if not w["filter_clean"]:
                return ready(
                    "Насос гудит и сразу отключается: сработал предохранитель. Нужно очистить фильтр. Никто не пострадал."
                )
            return ready(
                "Насос несколько раз глухо стучит и начинает работать ровно. За стеной шумит вода. Ада выдыхает: «Теперь можно идти в деревню. Давайте расскажем им всё вместе».",
                [("set", "pump_on", True)],
                ["Вода возвращается в деревню; цель выполнена"],
            )
        p.update(
            phase="clarify",
            text="Для этого действия пока нет подготовленного решения.",
            prompt="Выбери подходящее действие или опиши собственный исход в разделе «Решение ведущего».",
        )
        return p

    def roll(self, plan, die, source):
        require(type(die) is int and 1 <= die <= 20, "Нужно число от 1 до 20.")
        p = deepcopy(plan)
        total = die + p["check"]["bonus"]
        success = total >= p["check"]["dc"]
        prefix = "success" if success else "failure"
        p.update(
            phase="ready",
            roll={"die": die, "total": total, "success": success, "source": source},
            text=p[prefix],
            effects=p[prefix + "_effects"],
            changes=p[prefix + "_changes"],
        )
        return p

    def apply(self, state, plan, roll_die):
        """Apply only effects made by this rules module (never effects supplied by GPT)."""
        w = state["world"]
        actor = plan["action"]["actor"]
        extra = []
        if plan["roll"]:
            w["attempts"].append(plan["action"]["intent"])
            if w["help_for"] == actor:
                w["help_for"] = None
        for effect in plan["effects"]:
            op, *args = effect
            if op == "set":
                w[args[0]] = args[1]
            elif op == "clue":
                if args[0] not in state["clues"]:
                    state["clues"].append(args[0])
            elif op == "core":
                for c in state["characters"].values():
                    c["items"] = [x for x in c["items"] if x != "Сердечник"]
                w["core"] = args[0]
                if args[0] in state["characters"]:
                    state["characters"][args[0]]["items"].append("Сердечник")
            elif op == "heal":
                c = state["characters"][args[0]]
                c["hp"] = min(c["max_hp"], c["hp"] + args[1])
            elif op == "consume":
                state["characters"][args[0]]["items"].remove(args[1])
            elif op == "begin_combat":
                if not w["guard_disabled"]:
                    w.update(combat=True, round=1, acted=[], turn=next(iter(state["characters"])))
            elif op == "end_combat":
                self.end_combat(state)
            elif op == "retreat":
                state["characters"][args[0]]["location"] = "reception"
            elif op == "guard_damage":
                w["guard_hp"] = max(0, w["guard_hp"] - args[0])
                w["last_attacker"] = args[1]
        if w["combat"] and w["guard_hp"] == 0:
            w.update(guard_disabled=True, box_open=True)
            self.end_combat(state)
            extra.append("Страж отключается. Ада открывает ящик и помогает раненым.")
        if plan["in_combat"] and w["combat"]:
            if w["help_for"] == actor:  # unspent help expires at the end of the next combat turn
                w["help_for"] = None
            w["acted"].append(actor)
            active = [
                k for k, c in state["characters"].items() if c["hp"] > 0 and c["location"] == "workshop"
            ]
            remaining = [k for k in active if k not in w["acted"]]
            if not active:
                self.end_combat(state)
                state["scene"] = "reception"
            elif remaining:
                w["turn"] = remaining[0]
            else:
                target = w["last_attacker"] if w["last_attacker"] in active else active[0]
                die = roll_die()
                hit = die + 1 >= 10
                c = state["characters"][target]
                if hit:
                    c["hp"] = max(0, c["hp"] - 2)
                extra.append(
                    f"Страж: d20 {die} + 1 = {die + 1}. {c['name']}: " + ("−2 HP." if hit else "промах.")
                )
                active = [k for k in active if state["characters"][k]["hp"] > 0]
                if not active:
                    w.update(guard_disabled=True)
                    self.end_combat(state)
                    extra.append(
                        "Ада останавливает стража и помогает выбывшим до 1 HP. Она предлагает починить насос вместе."
                    )
                else:
                    w.update(acted=[], turn=active[0], round=w["round"] + 1)
        return extra

    def end_combat(self, state):
        w = state["world"]
        was_combat = w["combat"]
        w.update(combat=False, turn=None, acted=[], help_for=None)
        if was_combat:
            for c in state["characters"].values():
                c["hp"] = max(1, c["hp"])
