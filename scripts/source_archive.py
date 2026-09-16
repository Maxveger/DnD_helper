"""Export source from an explicit allowlist; exclude settings, user data and build outputs."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
destination = root / "dist/DnD-Helper-source.zip"
destination.parent.mkdir(exist_ok=True)
folders = ["dnd_helper", "tests", "scripts", "docs", ".github"]
files = [
    "README.md",
    "AGENTS.md",
    "CONTRIBUTING.md",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    ".gitignore",
    "settings.example.json",
    "start.sh",
    "start.cmd",
    "DnD.pdf",
]
paths = [root / file for file in files]
for folder in folders:
    paths.extend(p for p in (root / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
    for path in sorted(paths):
        archive.write(path, "DnD-Helper-source/" + path.relative_to(root).as_posix())
print(destination)
