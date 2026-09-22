# Project context

Start with `docs/README.md`, `docs/PROJECT_BRIEF.md` and `docs/NEXT_STEPS.md`.

- The repository has one supported product and route: the live-model GM studio at `/studio` (also served at `/`). Do not recreate the removed prepared-adventure demo, `/world`, Telegram or paid API paths.
- This is an assistant for a human beginner GM running a tabletop group, not a solo AI game. Players choose actions; the GM selects what to say and confirms consequences.
- The current slice is text-only, local web + the operator's own Codex subscription, using economical Luna. No implicit paid API fallback.
- A normal GM card uses one model request, including both dice outcomes. Roll, edits, confirmation and restore are local.
- Generated text is a GM draft. Mechanical effects are visible and transactional; text cannot mutate state.
- Never reset or play through the user's local save for tests. Use temporary directories. Keep credentials, user logs and local CLI installations out of Git.
- Distinguish source checks, live model measurements and published releases. Do not describe a stubbed or short probe as a tested full game.
