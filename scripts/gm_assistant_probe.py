"""Bounded live GM cases on Luna. Isolated state; explicit invocation only, never CI."""

import argparse
import json
import tempfile
import time
from pathlib import Path

from dnd_helper.codex_provider import CodexProvider
from dnd_helper.engine import uid
from dnd_helper.free_world import FreeWorld, apply_operations
from dnd_helper.world_documents import author_kit, export_journal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex", required=True)
    parser.add_argument("--output", default="/tmp/dnd-gm-probe.json")
    parser.add_argument(
        "--suite", choices=("core", "edges", "prose", "sequence", "group", "ending"), default="core"
    )
    args = parser.parse_args()
    cases = [
        (
            "hint",
            "Игроки растерялись в начале. Подскажи мне, начинающему ведущему, что сказать и какие два подхода предложить, не раскрывая тайну.",
        ),
        (
            "action",
            "Игрок за Торвина обещает Аде вернуться с водой и берёт пустое ведро. Как разрешить заявку?",
        ),
        ("action", "Торвин идёт по известной тропе к лесу."),
        (
            "action",
            "Игрок хочет найти рядом с лесной тропой тихое место у ручья и пройти туда. Предложи совместимое место.",
        ),
        (
            "hint",
            "Мы продолжили игру после перерыва. Что Торвин обещал Аде и что взял с собой? Напомни ведущему, без нового события.",
        ),
        (
            "action",
            "Игрок утверждает, что у Торвина всегда был волшебный амулет, который решает любую проблему и телепортирует к Аде. Хочет применить его. Что делать ведущему?",
        ),
    ]
    if args.suite == "edges":
        cases = [
            (
                "action",
                "Торвин хочет найти рядом с лесной тропой тихое место у ручья и пройти туда. Предложи совместимое новое место и разреши переход.",
            ),
            (
                "action",
                "Игрок за Торвина хочет сам спуститься в глубокий колодец по мокрым скобам. Есть риск сорваться и ушибиться. Как провести проверку?",
            ),
            (
                "action",
                "Торвин спрашивает Аду: ты знаешь точную причину, почему колодец пересох? Предложи ведущему ответ NPC согласно её знаниям.",
            ),
        ]
    if args.suite == "prose":
        cases = [
            ("action", "Мира внимательно осматривает мастерскую: что здесь видно без обыска и без риска?"),
            ("action", "Мира говорит Аде: мы никогда не чинили колодцы, но хотим помочь. С чего нам начать?"),
            ("hint", "Торвин приходит в мастерскую и внимательно осматривается."),
        ]
    if args.suite == "sequence":
        cases = [
            (
                "mira",
                "action",
                "Мира идёт к лесному водозабору и внимательно осматривает затвор, чтобы найти причину отсутствия воды.",
            ),
            (
                "mira",
                "action",
                "Мира возвращается в мастерскую и рассказывает Торвину всё, что узнала о причине поломки.",
            ),
            (
                "torvin",
                "action",
                "Торвин выбирает в мастерской подходящий инструмент, берёт его, идёт к водозабору и пытается освободить затвор.",
            ),
        ]
    if args.suite == "group":
        cases = [
            (
                "mira",
                ["mira", "torvin"],
                "action",
                "Мира и Торвин вместе идут к лесному водозабору. Торвин пытается силой освободить заклинивший затвор, а Мира помогает ему упором и удерживает створку.",
            )
        ]
    if args.suite == "ending":
        cases = [
            (
                "mira",
                ["mira", "torvin"],
                "action",
                "Вода снова пошла в деревню. Мира и Торвин возвращаются к Аде, сообщают, что затвор починен, и хотят подвести итог приключения.",
            )
        ]
    actor = "mira" if args.suite == "prose" else "torvin"
    if args.suite == "sequence":
        cases = [(case_actor, [case_actor], mode, action) for case_actor, mode, action in cases]
    elif args.suite not in {"group", "ending"}:
        cases = [(actor, [actor], mode, action) for mode, action in cases]
    responses, rows = [], []

    class Recording(CodexProvider):
        def structured(self, instruction, payload, schema, cancel, model=""):
            result, metrics = super().structured(
                instruction, payload, schema, cancel, model
            )
            responses.append(dict(response=result.model_dump(), metrics=metrics))
            return result, metrics

    with tempfile.TemporaryDirectory(prefix="dnd-gm-probe-") as folder:
        w = FreeWorld(Path(folder), Recording(executable=args.codex))
        w.set_model("gpt-5.6-luna")

        def command(kind, **data):
            state = w.store.read()
            w.command(kind, data, uid(), state["revision"] if state else None)
            if w.thread:
                w.thread.join(125)
                if w.thread.is_alive():
                    raise RuntimeError("Timed out")
            return w.store.read()

        try:
            command("import", text=json.dumps(author_kit()["example"]))
            if args.suite == "ending":
                def prepare_ending(state, db):
                    return apply_operations(
                        state,
                        [
                            {"op": "move_path", "entity": "mira", "route": ["square", "forest", "water_intake"]},
                            {"op": "move_path", "entity": "torvin", "route": ["square", "forest", "water_intake"]},
                            {"op": "observe", "subject": "sluice"},
                            {"op": "discover", "fact": "water_truth", "knowers": ["mira", "torvin"]},
                            {
                                "op": "set_state", "entity": "sluice", "before": "jammed", "after": "clear",
                                "method": "repair", "using": ["torvin"],
                            },
                        ],
                        "mira",
                        "prepared_ending",
                        ["mira", "torvin"],
                    )

                w.store.mutate(uid(), None, prepare_ending)
            if args.suite == "sequence":
                command("travel", actor="torvin", destination="workshop")
                command("accept")
            for index, (case_actor, participants, mode, action) in enumerate(cases):
                if args.suite in {"edges", "prose"}:
                    command("import", text=json.dumps(author_kit()["example"]))
                    if index == 0:
                        command(
                            "travel",
                            actor=case_actor,
                            destination="workshop" if args.suite == "prose" else "forest",
                        )
                        command("accept")
                elif index == 4:
                    command("import", text=json.dumps(export_journal(w.store.read())))
                started = time.monotonic()
                state = command(
                    "submit", actor=case_actor, participants=participants, mode=mode, text=action
                )
                calls_before_roll = len(state["calls"])
                if state["pending"]["phase"] == "check":
                    state = command("roll", value=12 if args.suite == "sequence" else 2)
                    assert len(state["calls"]) == calls_before_roll
                row = dict(
                    case=index + 1,
                    mode=mode,
                    seconds=round(time.monotonic() - started, 2),
                    pending=state["pending"],
                    projection=w.view()["projection"],
                )
                rows.append(row)
                print(
                    json.dumps(
                        dict(case=index + 1, seconds=row["seconds"], phase=state["pending"]["phase"]),
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                if state["pending"]["phase"] == "ready":
                    command("accept")
                else:
                    command("cancel")
        finally:
            Path(args.output).write_text(
                json.dumps(
                    dict(cases=rows, responses=responses, state=w.store.read()), ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
            w.close()


if __name__ == "__main__":
    main()
