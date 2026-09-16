# Project context

Read `docs/PROJECT_BRIEF.md` before changing product behavior. It records the user's decisions and takes priority over older design documents when they disagree.

- This is an assistant for a **human beginner GM running a tabletop group**, not a solo AI game. Players choose actions; the GM selects what to say and confirms consequences.
- The current slice is text-only, local web + the operator's own Codex subscription, using economical Luna. No implicit paid API fallback, Telegram delivery or voice work.
- A normal GM card uses **one** model request, including both dice outcomes. Roll, edits, known travel, confirmation and journal restore are local. Measure added requests before putting them on the live path.
- Custom starting scenarios, lore, immutable secrets and import/export of the game journal are part of the product. Preserve `dnd-world@1`, `dnd-world-log@1` and the separate prepared-adventure mode.
- Generated text is a GM draft and may contain mistakes. Mechanical effects are validated, visible and transactional; text cannot mutate state. Do not expose this secret-bearing screen/prompt as a player channel.
- Never reset or play through the user's local saves for tests. Use temporary directories. Keep credentials, logs containing user data and local CLI installation out of Git.
- Distinguish source checks, live model measurements and published Windows releases. Do not describe a short probe or simulated history as a tested full game.
