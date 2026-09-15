"""Build on the target OS. No API keys are needed or read by this script."""

import hashlib
import importlib.metadata
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def package_documentation(root, folder):
    from dnd_helper.adventures import author_kit

    shutil.copy2(root / "docs/PLAYER_SETUP.md", folder / "START-HERE.md")
    text = (root / "docs/ADVENTURE_FORMAT.md").read_text("utf-8")
    text = text.replace("../dnd_helper/resources/", "")
    (folder / "ADVENTURE_FORMAT.md").write_text(text, encoding="utf-8")
    (folder / "AUTHOR_KIT.md").write_text(author_kit()["prompt"], encoding="utf-8")
    for name in ("author-prompt.md", "example-adventure.json"):
        shutil.copy2(root / "dnd_helper/resources" / name, folder / name)


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
        "--collect-all",
        "imageio_ffmpeg",
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
    package_documentation(root, folder)
    shutil.copy2(root / "docs/THIRD_PARTY.md", folder / "THIRD-PARTY.md")
    notices = folder / "licenses"
    notices.mkdir(exist_ok=True)
    for dist in importlib.metadata.distributions():
        for file in dist.files or []:
            if file.name.lower().startswith(("license", "copying", "notice")) and ".dist-info" in str(file):
                dest = notices / dist.metadata["Name"] / file.name
                dest.parent.mkdir(exist_ok=True)
                shutil.copy2(dist.locate_file(file), dest)
    import imageio_ffmpeg

    for option, filename in (("-version", "FFMPEG-VERSION.txt"), ("-L", "FFMPEG-LICENSE.txt")):
        result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), option], capture_output=True, check=True)
        (notices / filename).write_bytes(result.stdout + result.stderr)
    name = "windows-x64" if windows else f"{sys.platform}-{platform.machine()}"
    archive = Path(
        shutil.make_archive(str(root / "dist" / f"DnD-Helper-{name}"), "zip", root / "dist", "DnD-Helper")
    )
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(f"Built: {archive}")


if __name__ == "__main__":
    main()
