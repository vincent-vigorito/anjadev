---
type: project
created: {DATE}
updated: {DATE}
---

# {PROJECT_NAME}

> {PROJECT_DESCRIPTION}

<!--
  Questo è il SOURCE editabile del project context (a mano).
  compose_claude_md.py unisce questo + SOUL.md + TOOLS.md in AGENTS.md (composed, letto
  nativo da Codex/Grok/OpenCode) e crea CLAUDE.md = @AGENTS.md (per Claude Code).
  Non dichiarare @SOUL.md/@TOOLS.md qui: il compose li aggiunge inline automaticamente.
  Token budget HOT: ~600. Mantienilo focalizzato e fresco.
-->

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
- **Senza MCP**: il wiki è file `.md` → `cat .anjawiki/wiki/index.md` (catalogo), `grep -ril --include='*.md' "<kw>" .anjawiki/wiki/`, `cat .anjawiki/wiki/<cat>/<slug>.md`. Dettagli in `.anjawiki/CLAUDE.md` → "Accesso bash-native".
