"""Build the studio application on the target OS."""

import hashlib
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    windows = sys.platform == "win32"
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "DnD-Helper",
        "--collect-data",
        "dnd_helper",
        "--hidden-import",
        "uvicorn.logging",
        "--hidden-import",
        "uvicorn.loops.auto",
        "--hidden-import",
        "uvicorn.protocols.http.h11_impl",
        "--hidden-import",
        "uvicorn.lifespan.on",
        "--specpath",
        "build",
        "scripts/freeze_entry.py",
    ]
    if windows:
        args.insert(3, "--windowed")
    subprocess.run(args, cwd=root, check=True)
    folder = root / "dist/DnD-Helper"
    shutil.copy2(root / "docs/PLAYER_SETUP.md", folder / "START-HERE.md")
    name = "windows-x64" if windows else f"{sys.platform}-{platform.machine()}"
    archive = Path(
        shutil.make_archive(str(root / "dist" / f"DnD-Helper-{name}"), "zip", root / "dist", "DnD-Helper")
    )
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"Built: {archive}")


if __name__ == "__main__":
    main()
