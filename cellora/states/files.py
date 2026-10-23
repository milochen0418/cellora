"""The Files panel: the workspace every kernel starts in (``pd.read_csv("sales.csv")`` reads from
here), shared by all notebooks: upload, download, delete, and a cell that reads a file."""

from __future__ import annotations

import asyncio
import dataclasses
import os
import re
import shutil
import time
from pathlib import Path

import reflex as rx

from cellora import paths
from cellora.states.lang import text

MAX_UPLOAD = 512 * 1024 * 1024
_READERS = {
    ".csv": 'pd.read_csv("{p}")',
    ".tsv": 'pd.read_csv("{p}", sep="\\t")',
    ".xlsx": 'pd.read_excel("{p}")',
    ".xls": 'pd.read_excel("{p}")',
    ".json": 'pd.read_json("{p}")',
    ".parquet": 'pd.read_parquet("{p}")',
}


@dataclasses.dataclass
class FileRow:
    name: str = ""
    path: str = ""
    size: str = ""
    modified: float = 0.0
    is_dir: bool = False
    readable: bool = False


def _human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def resolve(rel: str) -> Path | None:
    """A path inside the workspace (None for anything that would leave it)."""
    root = paths.files_dir().resolve()
    target = (root / (rel or "").lstrip("/")).resolve()
    return target if target == root or root in target.parents else None


def _safe_name(name: str) -> str:
    name = os.path.basename(name or "").strip().replace("\x00", "")
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
    return name if name not in ("", ".", "..") else f"upload-{int(time.time())}"


def _list(folder: str) -> list[FileRow]:
    base = resolve(folder)
    if base is None or not base.is_dir():
        return []
    rows = []
    for entry in sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if entry.name.startswith("."):
            continue
        try:
            st = entry.stat()
        except OSError:
            continue
        rel = str(entry.relative_to(paths.files_dir().resolve()))
        rows.append(FileRow(
            name=entry.name,
            path=rel,
            size="" if entry.is_dir() else _human(st.st_size),
            modified=st.st_mtime,
            is_dir=entry.is_dir(),
            readable=entry.suffix.lower() in _READERS,
        ))
    return rows


class FilesState(rx.State):
    folder: str = ""
    rows: list[FileRow] = []
    confirm: str = ""
    error: str = ""
    uploading: bool = False

    @rx.var
    def crumbs(self) -> list[str]:
        return [p for p in self.folder.split("/") if p]

    @rx.event
    async def refresh(self):
        self.rows = await asyncio.to_thread(_list, self.folder)

    @rx.event
    async def open_folder(self, rel: str):
        if resolve(rel) is not None:
            self.folder = rel.strip("/")
            await self.refresh()

    @rx.event
    async def up(self):
        self.folder = "/".join(self.crumbs[:-1])
        await self.refresh()

    @rx.event
    async def upload(self, files: list[rx.UploadFile]):
        self.error = ""
        base = resolve(self.folder)
        if base is None:
            return
        for f in files:
            target = base / _safe_name(f.name or "")
            tmp = target.with_name(target.name + ".part")
            size = 0
            try:
                with open(tmp, "wb") as out:
                    while chunk := await f.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_UPLOAD:
                            raise ValueError("too big")
                        out.write(chunk)
                os.replace(tmp, target)
            except ValueError:
                tmp.unlink(missing_ok=True)
                self.error = await text(self, "upload_too_big", name=f.name or "")
            except OSError as e:
                tmp.unlink(missing_ok=True)
                self.error = str(e)
        await self.refresh()

    @rx.event
    async def new_folder(self, name: str):
        base = resolve(self.folder)
        name = _safe_name(name)
        if base is not None and name:
            (base / name).mkdir(exist_ok=True)
            await self.refresh()

    @rx.event
    def ask_delete(self, rel: str):
        self.confirm = rel

    @rx.event
    def cancel_delete(self):
        self.confirm = ""

    @rx.event
    async def delete(self):
        rel, self.confirm = self.confirm, ""
        target = resolve(rel)
        if target is None or target == paths.files_dir().resolve():
            return
        try:
            if target.is_dir():
                await asyncio.to_thread(shutil.rmtree, target)
            else:
                target.unlink()
        except OSError as e:
            self.error = str(e)
        await self.refresh()

    @rx.event
    async def use_in_notebook(self, rel: str):
        """Adds a cell that reads the file into a DataFrame."""
        from cellora.states.notebook import NotebookState

        target = resolve(rel)
        if target is None:
            return
        reader = _READERS.get(target.suffix.lower())
        if reader is None:
            return
        stem = re.sub(r"\W+", "_", target.stem).strip("_").lower() or "data"
        name = stem if stem[0].isalpha() else f"df_{stem}"
        source = f"import pandas as pd\n\n{name} = {reader.format(p=rel)}\n{name}"
        return NotebookState.insert_source(source, "code")
