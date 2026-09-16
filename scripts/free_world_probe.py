"""Explicit, bounded live Codex smoke test. Never run in ordinary CI."""

import argparse
import json
import tempfile
import time
from pathlib import Path

from dnd_helper.codex_provider import CodexProvider
from dnd_helper.engine import uid
from dnd_helper.free_world import FreeWorld


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex", required=True)
    parser.add_argument("--output", default="/tmp/dnd-world-probe.json")
    parser.add_argument("--steps", type=int, default=4, choices=range(1, 7))
    args = parser.parse_args()
    actions = [
        "Обещаю Аде вернуться и помочь с водой. Беру пустое ведро у колодца, чтобы принести воду, если найду источник.",
        "Пока не хочу разбираться с колодцем. Иду по тропе к лесу.",
        "Осматриваюсь у лесной тропы: есть ли поблизости тихое место с ручьём? Если есть, хочу туда пройти.",
        "Возвращаюсь по своим следам к лесной тропе.",
        "Возвращаюсь на деревенскую площадь к Аде.",
        "Подхожу к Аде и спрашиваю: что я тебе обещала перед уходом и с каким предметом отправилась?",
    ]
    with tempfile.TemporaryDirectory(prefix="dnd-world-probe-") as folder:
        world = FreeWorld(Path(folder), CodexProvider(executable=args.codex))

        def command(kind, **data):
            s = world.store.read()
            world.command(kind, data, uid(), s["revision"] if s else None)
            deadline = time.monotonic() + 380
            while world.thread and world.thread.is_alive():
                if time.monotonic() > deadline:
                    raise RuntimeError("Probe timed out")
                world.thread.join(0.5)
            return world.store.read()

        command("new")
        report = []
        try:
            for action in actions[: args.steps]:
                started = time.monotonic()
                s = command("submit", text=action)
                if s["pending"]["phase"] == "check":
                    s = command("roll", value=15)
                row = {
                    "action": action,
                    "seconds": round(time.monotonic() - started, 2),
                    "pending": s["pending"],
                }
                report.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
                if s["pending"]["phase"] != "ready":
                    break
                command("accept")
        finally:
            Path(args.output).write_text(
                json.dumps({"turns": report, "state": world.store.read()}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            world.close()


if __name__ == "__main__":
    main()
