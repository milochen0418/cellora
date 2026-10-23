"""Where Cellora keeps its files at runtime.

Everything lives outside the project dir, so writing it never triggers a Reflex hot reload:

* ``notebooks/`` - one ``<id>.ipynb`` per notebook and its ``<id>.json`` (title, who made it)
* ``files/``     - the workspace: the kernels' working directory, where the Files panel uploads
* ``runtime/``   - the kernels' connection files and sockets

``CELLORA_HOME`` moves all of it. The default is ``~/Library/Application Support/cellora`` on
macOS and ``~/.local/share/cellora`` on Linux; the re-ddns catalog entry mounts a named Docker
volume there, so the files survive a reinstall. On re-ddns the notebooks themselves are items of
the shared collection ``notebooks`` instead (see :mod:`cellora.store`).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _default_home() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "cellora"


def home() -> Path:
    return Path(os.environ.get("CELLORA_HOME") or _default_home())


def notebooks_dir() -> Path:
    return home() / "notebooks"


def files_dir() -> Path:
    """The workspace every kernel starts in (``open("data.csv")`` finds the uploaded files)."""
    path = Path(os.environ.get("CELLORA_FILES_DIR") or home() / "files")
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_dir() -> Path:
    path = home() / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def socket_dir() -> Path:
    """A short private folder for the kernels' local sockets (a socket's path may be at most
    103 characters, which the macOS data folder already nearly is)."""
    base = Path("/tmp") if Path("/tmp").is_dir() else runtime_dir()
    path = Path(os.environ.get("CELLORA_SOCKET_DIR") or base / f"cellora-{os.getuid()}")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path
