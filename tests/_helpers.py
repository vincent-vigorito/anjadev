"""Helper condivisi dai test (importabile sia da pytest sia dagli script standalone)."""
from __future__ import annotations

import os


def cov_env() -> dict:
    """Env da propagare ai sottoprocessi perché coverage li misuri (CI). Vuoto altrimenti.

    I test sostituiscono HOME: se coverage è installato nel site-packages utente il
    sottoprocesso non lo troverebbe più, quindi la sua cartella viene aggiunta a PYTHONPATH."""
    if "COVERAGE_PROCESS_START" not in os.environ:
        return {}
    paths = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    try:
        import coverage
        pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(coverage.__file__)))
        if pkg_root not in paths:
            paths.append(pkg_root)
    except ImportError:
        pass
    return {"COVERAGE_PROCESS_START": os.environ["COVERAGE_PROCESS_START"],
            "PYTHONPATH": os.pathsep.join(paths)}
