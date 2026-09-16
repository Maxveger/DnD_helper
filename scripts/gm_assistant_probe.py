"""Six bounded live GM cases on Luna. Isolated state; explicit invocation only, never CI."""

import argparse
import json
import tempfile
import time
from pathlib import Path

from dnd_helper.codex_provider import CodexProvider
from dnd_helper.engine import uid
from dnd_helper.free_world import FreeWorld
from dnd_helper.world_documents import author_kit, export_journal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex", required=True)
    parser.add_argument("--output", default="/tmp/dnd-gm-probe.json")
    parser.add_argument("--suite", choices=("core", "edges"), default="core")
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
    responses, rows = [], []

    class Recording(CodexProvider):
        def structured(self, instruction, payload, schema, cancel, model=""):
            result, metrics = super().structured(instruction, payload, schema, cancel, model)
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
            for index, (mode, action) in enumerate(cases):
                if args.suite == "edges":
                    command("import", text=json.dumps(author_kit()["example"]))
                    if index == 0:
                        command("travel", actor="torvin", destination="forest")
                        command("accept")
                elif index == 4:
                    command("import", text=json.dumps(export_journal(w.store.read())))
                started = time.monotonic()
                state = command("submit", actor="torvin", mode=mode, text=action)
                calls_before_roll = len(state["calls"])
                if state["pending"]["phase"] == "check":
                    state = command("roll", value=2)
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
