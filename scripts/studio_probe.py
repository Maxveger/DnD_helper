"""Two-turn live /studio probe in a temporary database; uses the configured Codex account."""

import json
import sys
import tempfile
import threading
from pathlib import Path

from dnd_helper.studio import GameStudio, uid


def run(studio, kind, **data):
    state = studio.store.read()
    studio.command(kind, data, uid(), state["revision"] if state else None)
    if studio.thread:
        studio.thread.join(timeout=150)
        if studio.thread.is_alive():
            raise RuntimeError("Studio request did not finish")
    return studio.store.read()


def main():
    with tempfile.TemporaryDirectory(prefix="dnd-studio-probe-") as folder:
        studio = GameStudio(Path(folder))
        try:
            run(studio, "new")
            state = run(studio, "submit", mode="action", text="Позволь взглянуть на карту")
            if state["pending"]["phase"] not in {"review", "check"}:
                raise RuntimeError(
                    state["pending"].get("error", "No game card")
                    + " · protocol: "
                    + repr(studio.provider.app_failure)
                )
            if state["pending"]["phase"] == "check":
                raise RuntimeError("Safe request unexpectedly required a check")
            outcome = state["pending"]["selected_outcome"]
            state = run(
                studio,
                "accept",
                text=outcome["read_aloud"],
                apply_capsule=True,
                capsule=outcome["capsule_after"],
            )
            first_session = state["model_session_id"]
            state = run(studio, "submit", mode="advice", text="Сайрус уже дал Иво задаток?")
            if state.get("assistant_pending"):
                raise RuntimeError(state["assistant_pending"].get("error", "No private answer"))
            if state["model_session_id"] != first_session:
                raise RuntimeError("Second turn did not resume the same session")
            compact = None
            post_compact_advice = None
            if "--compact" in sys.argv[1:]:
                compact = studio.provider.compact(first_session, threading.Event())
                state = run(studio, "submit", mode="advice", text="Кратко: где сейчас Иво?")
                if state.get("assistant_pending"):
                    raise RuntimeError(state["assistant_pending"].get("error", "No post-compact answer"))
                post_compact_advice = state["side_chat"][-1]["answer"]
            print(
                json.dumps(
                    {
                        "session_resumed": True,
                        "game_summary": outcome["summary"],
                        "advice": state["side_chat"][-1]["answer"],
                        "compact": compact,
                        "post_compact_advice": post_compact_advice,
                        "calls": state["calls"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        finally:
            studio.close()


if __name__ == "__main__":
    main()
