# Changelog

All notable changes to the `anja` plugin.

## v0.10.0 — 2026-05-23

**Breaking change**: focus del plugin ristretto a "advanced knowledge management + semantic code search per progetti dev/research". Tool MCP AnjaHub-specific e content-generation rimossi.

### Removed (migrated)

I 4 MCP server seguenti sono stati rimossi da anjadev. Vivono ora nel plugin privato `anja-hub` di AnjaHub:

- `mcp_office_ops.py` — 13 tool gestione hub (workspace.task, agent.update, script.lifecycle, routine.lifecycle, goal.assign_agent, ecc.). Rinominato `mcp_hub_ops.py` con prefix tool `office.*` → `hub.*`.
- `mcp_images_server.py` — image generation via OpenRouter/Sora
- `mcp_videos_server.py` — video generation async polling 13 modelli
- `mcp_office_server.py` — generate docx/xlsx/pptx via pandoc/libreoffice/marp

**Razionale**: questi tool sono workflow tipici di un Personal AI Assistant (gestione hub + content creation), non di un plugin general-purpose per progetti dev/research. Un dev che installa anjadev su un suo monorepo Go o React non vuole content gen né tool che assumono struttura AnjaHub. Filosofia coerente con il split OSS commerciale di 2026-05-18.

### Migration

Chi aveva installato anjadev <0.10.0 e usava i tool rimossi:

1. Installare anche il plugin `anja-hub` (privato, fa parte del repo AnjaHub)
2. Eseguire `python3 anja-hub/scripts/migrate_workspaces_mcp_paths.py <hub-path> --apply` per riscrivere i `.mcp.json` dei workspace esistenti dai path/key vecchi (`anjadev/scripts/mcp_office_ops.py` + `anja_office_ops`) ai nuovi (`anja-hub/scripts/mcp_hub_ops.py` + `anja_hub_ops`).

Vedi `anja-plan.md:F-PluginSplit` nel repo AnjaHub per spec completa della migrazione.

### Plugin size

- LOC: ~16k → ~9k (-45%)
- MCP server: 6 → 2 (`mcp_memory_server` + `mcp_code_server`)
- File: ~60 → ~30

## v0.9.x e precedenti

Vedi git log per dettagli pre-0.10. Versioni 0.x sono pre-release, breaking change possibili ai minor bump.
