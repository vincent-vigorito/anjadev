---
name: evolve-skills
description: Workflow review per auto-improvement delle skill (pattern Hermes). Legge inbox PostToolUse hook → invoca LLM haiku per analizzare se ogni invocazione skill è memorabile (edge case, pattern utile, esempio illustrativo) → propone patch alla SKILL.md → utente conferma → apply via skill.patch. Da usare con /anja-evolve-skills o cron periodico.
version: 1.0.0
category: meta
tags: [skill-evolution, hermes-pattern, auto-improvement, meta]
platforms: [macos, linux]
requires_tools: [Bash, mcp__anja_memory__skill.patch]
---

# Skill: evolve-skills

Workflow di auto-improvement delle skill anja. Implementa il pattern Hermes "skills learn from usage": le skill stesse possono ricevere patch dopo invocations che hanno scoperto pattern memorabili.

## Architettura a 3 layer

```
[Skill invocation]
       ↓ (PostToolUse hook skill_evolution.py)
~/.anja/skill_evolution_inbox.jsonl    ← Layer 1: raw events
       ↓ (evolve.py + claude haiku review)
~/.anja/skill_evolution_proposals.jsonl  ← Layer 2: LLM-vetted proposals
       ↓ (user confirm + skill.patch)
SKILL.md updated + backup in .history/   ← Layer 3: applied changes
```

## Quando attivare

Sì:
- Periodicamente (suggerito: weekly via routine, o on-demand quando user dice "evolvi le skill")
- Dopo una giornata di uso intenso di skill custom
- Quando l'utente chiede "controlla se ci sono pattern memorabili dalle ricerche"

No:
- Subito dopo ogni invocation (rumore + costo LLM)
- Con inbox vuota — skip silenzioso

## Pre-condizioni

- Tool `Bash` disponibile per eseguire `evolve.py`
- `claude` CLI in PATH (per review LLM haiku)
- File `~/.anja/skill_evolution_inbox.jsonl` esistente (creato dal PostToolUse hook automaticamente)

## Workflow

### Step 1 — Genera proposals via LLM review

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/evolve-skills/scripts/evolve.py" --batch 5
```

Output: aggiunge righe in `~/.anja/skill_evolution_proposals.jsonl` con format:
```json
{
  "ts": "2026-05-23T...",
  "source_entry_ts": "...",
  "skill": "research-duckduckgo",
  "memorable": true,
  "rationale": "Edge case: query con synonym multipli ritorna 0 → variant più ampia funziona",
  "suggested_section": "Edge case",
  "patch_proposal": {
    "section_to_append": "## Edge case\n\n- Se 0 risultati su query con multi-synonym, prova riformulazione più ampia (un singolo concetto)..."
  },
  "applied": false
}
```

### Step 2 — Review proposte con utente

Mostra le proposte non ancora applicate (`applied=false`) all'utente in markdown:

```
🔍 Proposte di evolution skill (3 da review):

[1] skill: research-duckduckgo
    rationale: "Edge case: query specifiche ritornano 0..."
    patch: aggiungere sezione "Edge case" con esempio

[2] skill: csv-to-markdown
    rationale: "Pattern: CSV con header missing → assume row 0"
    patch: aggiungere note in "Best practice"

[3] skill: orchestrate-hub
    rationale: "User edits frequent sul 'cadence' → suggerisci preset"
    patch: aggiungere section "Common edits"
```

Per ognuna chiedere: **approve / skip / edit** (l'utente può rifinire il testo).

### Step 3 — Apply via skill.patch

Per ogni proposta approved, invoca tool MCP:
```
skill.patch(
  name="research-duckduckgo",
  old_text="## Anti-pattern",
  new_text="## Edge case\n\n- Se 0 risultati...\n\n## Anti-pattern"
)
```

Mark `applied: true` nel proposals.jsonl.

### Step 4 — Audit log

Append in `~/.anja/skill_evolution_applied.jsonl`:
```json
{"ts": "...", "skill": "research-duckduckgo", "rationale": "...", "patch_summary": "added Edge case section"}
```

## Output schema review LLM

Il prompt review chiede al modello (haiku default) di rispondere SEMPRE in JSON:

```json
{
  "memorable": true|false,
  "rationale": "1 frase",
  "suggested_section": "Edge case | Best practice | Example | None",
  "patch_proposal": {
    "section_to_append": "## ... markdown ..."
  }
}
```

## Criteri "memorabile"

Sì:
- Edge case scoperto (skill ha fallito su input non triviale)
- Output controintuitivo / sorprendente
- Pattern args/parameter ottimale
- Esempio concreto utile

No:
- Esecuzione routine normale
- Errore di config (es. API key missing) — non è learning della skill, è setup
- Output generico

## Safety

- **No auto-apply**: ogni patch richiede conferma utente esplicita
- **Backup**: prima della patch, `skill.patch` salva backup in `<skill>/.history/<ts>.SKILL.md` (vedi SE-4)
- **Rollback**: `skill.rollback(name)` ripristina ultimo backup
- **Marker incrementale**: `~/.anja/skill_evolution_last_processed.txt` evita re-review delle stesse entry

## Anti-pattern

1. ❌ Skippare conferma utente — può portare a drift incontrollato delle skill
2. ❌ Review LLM sull'inbox intera senza batch — costa troppo
3. ❌ Auto-apply proposte "memorable: true" senza human in the loop
4. ❌ Ignorare il marker → re-process stesse entry → rumore

## Integration con routine

Può girare come routine cron settimanale:

```yaml
# <hub>/routines/evolve-skills-weekly.yaml
name: evolve-skills-weekly
scope: hub
schedule: "0 18 * * 0"  # Sunday 18:00
prompt: |
  Esegui workflow evolution delle skill anja.
  1. skill.load("evolve-skills")
  2. Run bash: python3 <plugin-root>/skills/evolve-skills/scripts/evolve.py --batch 20
  3. Per ogni proposta memorable, mostra a user via telegram con buttons approve/skip
tools: [Bash]
output:
  - type: telegram
    chat_id: "{{TELEGRAM_CHAT_ID}}"
    template: "🧠 Skill evolution review settimanale — {memorable_count} proposte"
```
