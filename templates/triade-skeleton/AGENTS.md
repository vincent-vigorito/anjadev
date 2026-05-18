---
type: project
created: {DATE}
updated: {DATE}
---

# {PROJECT_NAME}

> {PROJECT_DESCRIPTION}

<!--
  AGENTS.md è il file always-loaded di project-level (cross-tool: CC, OpenCode, Cursor, Aider, ...).
  Sostituisce il vecchio CLAUDE.md (mantenuto come symlink → AGENTS.md per back-compat).
  Token budget HOT: ~600. Mantienilo focalizzato e fresco.

  IMPORTANTE: i marker `@SOUL.md` e `@TOOLS.md` qui sotto sono import dichiarativi:
  Claude Code (e OpenCode) li sostituiscono inline col contenuto del file. Così la triade
  completa è sempre caricata in ogni sessione, anche da CLI puro nel progetto.
-->

@SOUL.md
@TOOLS.md

## Stato corrente

<una frase + data, es: "MVP completo, 2 progetti registrati, ultima sync 2026-05-07.">

## Tipo

`{PROJECT_TYPE}` — {PROJECT_TYPE_DESCRIPTION}

## Convenzioni

- Pattern di codice rilevanti per questo progetto
- Anti-pattern noti (cose già provate che non funzionano)
- Tooling specifico (Python 3.12, TypeScript, Go, ...)
- Stile di commit, branch naming

## Workflow tipici

- Come si fa X (link a wiki/concepts/<x>)
- Come si fa Y
- Build/test/deploy

## Architettura essenziale

2-3 frasi sull'architettura. Per dettagli vedi `[[wiki/index]]` o `[[wiki/entities/...]]`.

## Note operative

- Dove vivono i log: `<path>`
- Dove vive il deploy: `<path>`
- Dove vivono i secret: `<path>` (gitignored)
- Comandi rapidi più usati

## Memoria collegata

- `SOUL.md` — preferenze user, feedback memorabili, identità agent
- `TOOLS.md` — capabilities (auto-generato)
- `.anjawiki/wiki/` — knowledge strutturata (entities/concepts/sources/sessions)
