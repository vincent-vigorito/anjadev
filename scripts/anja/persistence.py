"""Scritture atomiche con compare-and-swap per writer cooperanti locali."""
from __future__ import annotations

import fcntl
import hashlib
import os
import stat
import tempfile
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

MISSING = "missing"


class PersistenceError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.result = {"error": message, "code": code, **details}


def persistence_errors(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        try:
            return handler(*args, **kwargs)
        except PersistenceError as exc:
            return exc.result
    return wrapped


def revision(text: str | None) -> str:
    return MISSING if text is None else "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_text(path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            return stream.read()
    except FileNotFoundError:
        return None


def check_revision(path: Path, text: str | None, expected: str | None) -> str:
    actual = revision(text)
    if expected is not None and expected != actual:
        raise PersistenceError("revision_conflict", "document changed; read it again before retrying",
                               path=str(path), expected_revision=expected, current_revision=actual)
    return actual


@contextmanager
def file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Il lock deve sopravvivere al replace del documento; non cancellare il lockfile.
    fd = os.open(path.with_name("." + path.name + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _publish(path: Path, text: str, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            if not exclusive and path.exists():
                os.fchmod(stream.fileno(), stat.S_IMODE(path.stat().st_mode))
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(tmp, path)
        else:
            os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def create_text(path: Path, text: str):
    """Pubblicazione esclusiva: il path non è mai visibile con contenuto parziale."""
    _publish(path, text, exclusive=True)


def write_text(path: Path, text: str, expected_revision: str):
    path = path.resolve()
    with file_lock(path):
        check_revision(path, read_text(path), expected_revision)
        _publish(path, text)
    return revision(text)


def delete_text(path: Path, expected_revision: str):
    with file_lock(path.resolve()):
        check_revision(path, read_text(path), expected_revision)
        path.unlink()


def write_many(changes: dict, rename=None):
    """Preflight di tutte le revisioni sotto lock; pubblicazioni atomiche per file.

    Per rename: crea il nuovo nome prima di aggiornare i link, rimuove il vecchio
    solo alla fine. Un'interruzione può lasciare entrambi, ma non link senza target.
    """
    from contextlib import ExitStack
    paths = {path.resolve() for path in changes}
    if rename:
        source, target, expected = rename
        paths.update((source.resolve(), target.resolve()))
    with ExitStack() as stack:
        for path in sorted(paths):
            stack.enter_context(file_lock(path))
        for path, (_text, expected_rev) in changes.items():
            check_revision(path, read_text(path), expected_rev)
        if rename:
            check_revision(source, read_text(source), expected)
            if target.exists() or target.is_symlink():
                raise PersistenceError("target_exists", "rename target already exists", path=str(target))
            os.link(source, target, follow_symlinks=False)
        for path, (text, _expected_rev) in changes.items():
            _publish(path.resolve(), text)
        if rename:
            source.unlink()
    return {str(path): revision(text) for path, (text, _expected) in changes.items()}
