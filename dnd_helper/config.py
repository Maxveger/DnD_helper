"""Location of local studio data."""

import os
from pathlib import Path


def data_directory():
    if os.environ.get("DND_HELPER_DATA_DIR"):
        return Path(os.environ["DND_HELPER_DATA_DIR"])
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")
        return Path(root) / "DnDHelper"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "dnd-helper"
