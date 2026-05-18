---
title: Log
type: log
created: "{{CREATED}}"
---

# Chronological log

Append-only. Fixed prefix for parsing: `## [YYYY-MM-DD] type | description`.

Types: `init`, `ingest`, `query`, `refresh`, `lint`, `session`.

Useful command: `grep "^## \[" wiki/log.md | tail -10`.

---

## [{{CREATED}}] init | initial scaffolding ({{INIT_MODE}})
