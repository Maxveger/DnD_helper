"""Release promotion rejects changed binaries and unexpected archive paths."""

import hashlib
from zipfile import ZipFile

import pytest

from scripts.publish_release import ASSETS, digest, verify_archive


def artifact(tmp_path, *, extra=None, bad_checksum=False):
    file = tmp_path / "artifact.zip"
    binary = b"example Windows package"
    checksum = "0" * 64 if bad_checksum else hashlib.sha256(binary).hexdigest()
    with ZipFile(file, "w") as z:
        z.writestr(ASSETS[0], binary)
        z.writestr(ASSETS[1], f"{checksum}  {ASSETS[0]}\n")
        z.writestr(ASSETS[2], b"example source archive")
        if extra:
            z.writestr(extra, "unexpected")
    return file


def test_verified_release_keeps_exact_assets(tmp_path):
    archive = artifact(tmp_path)
    files = verify_archive(archive, digest(archive), tmp_path / "output")
    assert [p.name for p in files] == list(ASSETS)
    assert files[0].read_bytes() == b"example Windows package"
    with pytest.raises(ValueError, match="digest mismatch"):
        verify_archive(archive, "0" * 64, tmp_path / "other")
    assert not (tmp_path / "other").exists()


def test_release_rejects_unexpected_paths(tmp_path):
    archive = artifact(tmp_path, extra="../outside.txt")
    with pytest.raises(ValueError, match="Unexpected"):
        verify_archive(archive, digest(archive), tmp_path / "output")
    assert not (tmp_path / "outside.txt").exists()


def test_release_rejects_changed_windows_package(tmp_path):
    archive = artifact(tmp_path, bad_checksum=True)
    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_archive(archive, digest(archive), tmp_path / "output")
