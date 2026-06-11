#!/usr/bin/env bash
# bump.sh — single source of truth per la versione del plugin anjadev.
#
# Aggiorna in UN colpo tutti i file di versione del manifest così non vanno più in
# deriva (era la causa del "già aggiornato" dopo un fix: CC versiona per NUMERO, non
# per git SHA — se il numero non cambia non ricrea la cache e gira il codice vecchio).
#
# File toccati:
#   .claude-plugin/plugin.json       (versione plugin — quella che CC confronta)
#   .claude-plugin/marketplace.json  (versione marketplace + versione plugin elencata)
#   .codex-plugin/plugin.json        (versione plugin Codex)
#
# Uso:  ./bump.sh 0.18.1
# Poi:  aggiorna CHANGELOG.md, git commit, git push, git tag v0.18.1 && git push --tags
set -euo pipefail

NEW="${1:-}"
if [[ ! "$NEW" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "uso: $0 <major.minor.patch>   (es. $0 0.18.1)" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "$0")" && pwd)"
FILES=(
  "$ROOT/.claude-plugin/plugin.json"
  "$ROOT/.claude-plugin/marketplace.json"
  "$ROOT/.codex-plugin/plugin.json"
)

for f in "${FILES[@]}"; do
  [[ -f "$f" ]] || { echo "ERRORE: manca $f" >&2; exit 1; }
  NEW="$NEW" perl -i -pe 's/("version"\s*:\s*")[^"]*(")/$1 . $ENV{NEW} . $2/ge' "$f"
done

echo "✓ versione → $NEW. Stato dei manifest:"
grep -Hn '"version"' "${FILES[@]}"

# Sanity: tutte le occorrenze devono ora essere $NEW
if grep -h '"version"' "${FILES[@]}" | grep -qv "\"$NEW\""; then
  echo "⚠️  ATTENZIONE: qualche \"version\" non è $NEW — controlla sopra." >&2
  exit 1
fi

cat <<EOF

Prossimi passi:
  1. aggiorna CHANGELOG.md con la voce v$NEW
  2. git add -A && git commit -m "chore(release): v$NEW"
  3. git push && git tag v$NEW && git push --tags
  4. in un progetto: /plugin update anja@anjadev   (ora CC vede $NEW > precedente → ricrea la cache)
EOF
