"""Attiva coverage nei sottoprocessi (server MCP via stdio, steward, hook) quando la CI
esporta PYTHONPATH=<root>/tests/_coverage_hook e COVERAGE_PROCESS_START=<root>/pyproject.toml.
Senza quelle env non fa nulla.

I sottoprocessi girano con cwd arbitrario (cartelle temporanee): `source` in pyproject usa
${ANJA_COV_ROOT} (espanso da coverage) e i file dati vanno sempre nella root del plugin."""
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("ANJA_COV_ROOT", str(_ROOT))
os.environ.setdefault("COVERAGE_FILE", str(_ROOT / ".coverage"))
try:
    import coverage
    coverage.process_startup()
except Exception:  # coverage non installato o non configurato: ignorare
    pass
