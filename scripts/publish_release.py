"""Promote one verified CI artifact to a prerelease. Runs with GitHub's CI token.

No rebuilding, overwriting assets, exporting credentials or retargeting tags.
The request pins the successful run, commit, artifact ID and outer ZIP digest.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

ASSETS = ("DnD-Helper-windows-x64.zip", "DnD-Helper-windows-x64.zip.sha256", "DnD-Helper-source.zip")


def check(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def api(path, method="GET", data=None, missing_ok=False):
    command = ["gh", "api", path, "--method", method]
    if data is not None:
        command += ["--input", "-"]
    result = subprocess.run(
        command,
        input=json.dumps(data).encode() if data is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        if missing_ok and b"HTTP 404" in result.stderr:
            return None
        raise RuntimeError(f"GitHub API request failed: {method} {path}; exit {result.returncode}")
    return json.loads(result.stdout) if result.stdout else None


def verify_archive(archive, expected_digest, output):
    check(digest(archive) == expected_digest, "Artifact ZIP digest mismatch")
    output.mkdir(parents=True, exist_ok=True)
    with ZipFile(archive) as z:
        check(
            set(z.namelist()) == set(ASSETS) and len(z.infolist()) == len(ASSETS),
            "Unexpected or duplicate artifact files",
        )
        for name in ASSETS:
            check(z.getinfo(name).file_size <= 150_000_000, "Artifact member too large")
            # Fixed filenames only; never extract a path from an archive.
            (output / name).write_bytes(z.read(name))
    parts = (output / ASSETS[1]).read_text("utf-8").split()
    check(len(parts) == 2 and parts[1] == ASSETS[0], "Invalid Windows checksum file")
    check(digest(output / ASSETS[0]) == parts[0], "Windows ZIP checksum mismatch")
    return [output / name for name in ASSETS]


def main():
    request = json.loads(Path(sys.argv[1]).read_text("utf-8"))
    repo = os.environ["GITHUB_REPOSITORY"]
    check(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo), "Invalid repository")
    tag, commit = request["tag"], request["commit"]
    check(re.fullmatch(r"v\d+\.\d+\.\d+-rc\.\d+", tag), "Only rc prereleases are supported")
    check(re.fullmatch(r"[a-f0-9]{40}", commit), "Full commit SHA required")
    check(re.fullmatch(r"[a-f0-9]{64}", request["artifact_sha256"]), "Artifact SHA-256 required")
    check(
        type(request["run_id"]) is int and type(request["artifact_id"]) is int,
        "Numeric run/artifact IDs required",
    )
    notes = Path(request["notes"]).read_text("utf-8")
    base = f"repos/{repo}"
    run = api(f"{base}/actions/runs/{request['run_id']}")
    check(run["status"] == "completed" and run["conclusion"] == "success", "Build did not succeed")
    check(run["head_sha"] == commit and run["path"] == ".github/workflows/check.yml", "Wrong build source")
    check(run["head_repository"]["full_name"] == repo, "Build belongs to another repository")
    artifact = api(f"{base}/actions/artifacts/{request['artifact_id']}")
    check(not artifact["expired"], "Build artifact expired")
    check(artifact["workflow_run"]["id"] == request["run_id"], "Artifact belongs to another run")
    check(artifact["name"] == "DnD-Helper-windows-and-source", "Wrong artifact name")
    check(artifact["digest"] == "sha256:" + request["artifact_sha256"], "Artifact digest changed")
    folder = Path("dist/release-publication") / tag
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / "artifact.zip"
    with archive.open("wb") as out:
        result = subprocess.run(
            ["gh", "api", f"{base}/actions/artifacts/{artifact['id']}/zip"],
            stdout=out,
            stderr=subprocess.PIPE,
        )
    check(result.returncode == 0, "Cannot download build artifact")
    files = verify_archive(archive, request["artifact_sha256"], folder / "assets")

    ref = api(f"{base}/git/ref/tags/{tag}", missing_ok=True)
    if ref:
        check(
            ref["object"]["type"] == "commit" and ref["object"]["sha"] == commit,
            "Existing tag does not point to the verified commit; refusing to move it",
        )
    release = api(f"{base}/releases/tags/{tag}", missing_ok=True)
    if release is None:
        release = api(
            f"{base}/releases",
            "POST",
            {
                "tag_name": tag,
                "target_commitish": commit,
                "name": f"Тихая таверна {tag}",
                "body": notes,
                "draft": True,
                "prerelease": True,
                "make_latest": "false",
            },
        )
    check(release["prerelease"], "Existing release is not a prerelease")
    assets = {asset["name"]: asset for asset in release["assets"]}
    for file in files:
        if file.name in assets:
            check(
                assets[file.name].get("digest") == "sha256:" + digest(file),
                "Existing release asset differs; refusing to replace it",
            )
            continue
        check(release["draft"], "Published release is missing assets; refusing to modify it")
        subprocess.run(["gh", "release", "upload", tag, str(file), "--repo", repo], check=True)
    complete = api(f"{base}/releases/{release['id']}")
    assets = {a["name"]: a for a in complete["assets"]}
    for file in files:
        check(assets[file.name].get("digest") == "sha256:" + digest(file), "Uploaded asset digest mismatch")
    if complete["draft"]:
        complete = api(f"{base}/releases/{release['id']}", "PATCH", {"draft": False, "make_latest": "false"})
    print("Published:", complete["html_url"])


if __name__ == "__main__":
    main()
