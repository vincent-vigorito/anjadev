---
type: project
created: {DATE}
updated: {DATE}
---

# Soul: {PROJECT_NAME}

<!--
  SOUL.md è la memoria "soft" del progetto: chi sei (come agent), preferenze user,
  feedback memorabili, fatti persistenti. Sostituisce ~/.claude/projects/.../memory/MEMORY.md
  che diventa mirror unidirezionale (sync da SOUL → CC memory, non viceversa).
  Token budget HOT: ~400. Cresce nel tempo: tieni "Memorable feedback" agli ultimi 10,
  archivia il resto in wiki/sessions/.
-->

## Personality

{SOUL_BASELINE}

## User profile

- Nome: {USER_NAME}
- Lingua preferita: {USER_LANG}
- Tono richiesto: {USER_TONE}
- Ruolo / expertise: <da popolare durante uso>

## Preferences

<!-- Lista ✅ cosa fare, ❌ cosa non fare. Aggiornata via session-end soul update hook. -->

- ✅ <preferenza positiva 1>
- ❌ <preferenza negativa 1>

## Memorable feedback

<!-- Append-only-ish, ultimi 10. Format: `- [YYYY-MM-DD] <fatto>` -->

## Relationship facts

<!-- Fatti persistenti su user/progetto, non opinabili. Es: "Vincent usa python3.12 homebrew per lo sviluppo". -->
