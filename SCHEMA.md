# `.anjawiki/` schema — wire format pubblico

> Specifica del layout e del formato dei file dentro `.anjawiki/`. Questo documento è il **contratto pubblico** che consumatori esterni (hub AnjaHub, tool di sync, IDE plugin, script di terze parti) possono assumere quando leggono o scrivono in un wiki anja.
>
> Per il **manuale operativo** (workflow ingest/query/refresh/lint pensato per LLM agent dentro CC), vedi `.anjawiki/CLAUDE.md` scaffoldato in ogni progetto.

## Versioning

- File `.anjawiki/.schema-version` contiene una stringa semver-like (es. `1.0`).
- **MAJOR** bump = rottura layout/frontmatter required/log format → consumatori devono fare migration.
- **MINOR** bump = aggiunte non-breaking (nuove sotto-cartelle ignorabili, nuovi frontmatter opzionali).
- Current: **1.1** (aggiunge la famiglia trust/lifecycle opzionale, v. sotto).

## Layout cartelle

```
<project-root>/
├── .anjawiki/
│   ├── .schema-version         ← versione schema (text, semver)
│   ├── .secrets.env            ← API keys (gitignored, mai committare)
│   ├── config.json             ← config plugin (memory budget, ecc.)
│   ├── meta.yaml               ← identità del progetto (token, name, type)
│   ├── CLAUDE.md               ← manuale operativo del wiki per LLM agent
│   ├── code-index.db           ← sqlite-vec store opzionale (gitignored)
│   ├── raw/                    ← fonti immutabili (mai modificate da agent)
│   │   └── <topic>/...
│   └── wiki/                   ← contenuto generato (owned by agent)
│       ├── index.md            ← SPECIAL: catalogo semantico
│       ├── log.md              ← SPECIAL: append-only eventi
│       ├── overview.md         ← SPECIAL: sintesi corrente
│       ├── roadmap.md          ← SPECIAL: task strutturati
│       ├── entities/<slug>.md
│       ├── concepts/<slug>.md
│       ├── sources/<slug>.md
│       ├── analysis/<slug>.md
│       └── sessions/YYYY-MM-DD/<HHMMSS-cli-claude-XXXX>.md
└── (SOUL.md, AGENTS.md, TOOLS.md, .mcp.json — fuori da .anjawiki/, triade CC)
```

## meta.yaml

YAML, identità del progetto. **Single source of truth** per token e tipo.

```yaml
token: anja_<uuid7-canonical>     # RFC 9562 UUIDv7, time-sortable
name: <project-name>
type: dev | personal | research | business | automation
created: YYYY-MM-DD
tags: [tag1, tag2]
```

## Frontmatter pagine wiki

YAML frontmatter delimitato da `---`. Campi **required** per ogni pagina:

```yaml
---
title: <stringa leggibile>
type: entity | concept | source | analysis | session | overview | index | log | roadmap
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

Campi **opzionali**:
- `sources: [slug-1, slug-2]` (pagine source di provenienza)
- `tags: [tag1, tag2]`
- `source_path: ../../raw/<topic>/<file>` (solo `type: source`)
- `subtype: codebase-snapshot` (solo source di code refresh)
- `git_sha: <sha>` + `analyzed_at: YYYY-MM-DDTHH:MM:SSZ` (snapshot)
- `transient: true` (analysis cancellabili, es. lint report)
- `question: "..."` (analysis che nasce da query)

### Trust & lifecycle (schema 1.1, semantica = OKF v0.2 §5)

Famiglia **opzionale**; l'assenza è informativa, mai un errore. Semantica identica
all'[Open Knowledge Format v0.2](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
così un export OKF è lossless.

```yaml
generated: { by: anjadev/0.20.0, at: 2026-07-26T18:30:00Z }   # chi ha scritto l'ultima modifica (auto, ogni write)
verified: [{ by: human:vincent, at: 2026-07-26T18:35:00Z }]   # chi ha CONFERMATO (append via wiki.verify)
status: stable            # draft | stable | deprecated (assente = stable)
stale_after: 2026-12-31   # data assoluta oltre cui il contenuto va ri-verificato
```

- **Convenzione attori** (OKF §7): `<producer>/<version>` per agent/tool,
  `human:<id>` per persone, `process:<id>` per processi automatici.
- **Trust tier derivato** (mai memorizzato): nessun `verified` → *unverified*;
  solo attori non-`human:` → *machine-confirmed*; almeno un `human:` → *human-reviewed*.
- `generated` ≠ `verified`: chi scrive non è chi conferma. I writer stampano
  `generated` a ogni upsert; `verified` si appende solo via `wiki.verify`.
- Wire format: flow-style inline su una riga (il parser naive li tratta come
  stringhe opache e fa round-trip verbatim). Consumer: trattare un mapping nudo
  `verified: { by, at }` come lista da 1 elemento.
- Lint: pagine con `oggi >= stale_after` sono warning `stale-after`; il summary
  riporta i conteggi per trust tier.

## Wikilinks

```
[[slug]]
[[slug|label custom]]
[[slug#section]]
[[slug#section|label custom]]
```

Regex parser: `\[\[([^\]|#\s]+)(#[^\]|]+)?(\|[^\]]+)?\]\]`. Slug = nome file senza `.md`, in kebab-case.

## Slug naming

| Tipo | Pattern | Esempio |
|---|---|---|
| entity | kebab-case nome | `auth-service` |
| concept | kebab-case nome | `event-driven-architecture` |
| source | `YYYY-MM-DD-<slug>` o `codebase-snapshot-YYYY-MM-DD` | `2026-05-18-paper-x` |
| analysis | kebab-case tema | `auth-comparison` |
| session | `HHMMSS-cli-claude-XXXX` dentro `sessions/YYYY-MM-DD/` | `194849-cli-claude-d9e6` |
| roadmap | (special, file unico) | `roadmap.md` |

## Log format (strict)

`wiki/log.md` è append-only. Parser regex: `^## \[(\d{4}-\d{2}-\d{2})\] ([\w-]+) \| (.+)$`.

```
## [YYYY-MM-DD] tipo | descrizione breve in una riga
```

Tipi convenzionali: `init`, `init-analyze`, `ingest`, `query`, `refresh`, `lint`, `session`, `decision`, `milestone`, `note`. Custom kebab-case ammessi.

## Roadmap format

`wiki/roadmap.md` ha 3 sezioni `## Open`, `## Done`, `## Blocked`. Ogni task una riga:

```
- [ ] (P0|P1|P2|P3) <title> | est: <free> | owner: <name> | added: YYYY-MM-DD
- [x] <title> | owner: <name> | done: YYYY-MM-DD | took: <free>
- [ ] (P1) <title> | blocked_by: <reason> | added: YYYY-MM-DD
```

Parser implementazione canonica: `scripts/roadmap_io.py`. Metadata inline split su `|`, key in {`est`, `owner`, `added`, `done`, `took`, `blocked_by`, `due`, `link`}.

## File speciali al root di wiki/

Esenti da check di "orphan" (sono entry-point, non linkati da altre pagine):

- `index.md` — catalogo navigabile per umani
- `log.md` — episodi cronologici append-only
- `overview.md` — sintesi tesi corrente del progetto
- `roadmap.md` — task strutturati

## Garanzie per consumatori esterni

Un consumatore esterno (es. AnjaHub) può assumere quanto segue per schema-version `1.0`:

1. **Path layout immutabile**: `wiki/{entities,concepts,sources,analysis,sessions}/` esistono o sono creabili.
2. **Frontmatter required** sempre presente sulle pagine generate dal plugin.
3. **Wikilinks `[[slug]]`** sono path-relative-free: lo slug è univoco a livello di wiki, no namespace.
4. **Log parsing** via regex sopra è stabile.
5. **Slug convention** vale per tutte le pagine generate dal plugin (utenti potrebbero violarla).
6. **Encoding**: UTF-8, line ending `\n`.
7. **Time zone**: date in formato ISO 8601 local (`YYYY-MM-DD`); timestamp full opzionali in ISO 8601 con `Z` per UTC.

## Cambiamenti dalla versione precedente

- **1.0** (2026-05-18): prima versione formalizzata. Estrazione dal manuale operativo `.anjawiki/CLAUDE.md`.
