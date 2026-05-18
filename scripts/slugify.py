#!/usr/bin/env python3
"""
slugify.py — generate consistent kebab-case slugs from titles.

Usato da `/anja-ingest` (e altri comandi che generano slug) per nomare
le pagine del wiki in modo consistente. ASCII-only, kebab-case.

Esempi:
  "Karpathy LLM Wiki Pattern"   -> "karpathy-llm-wiki-pattern"
  "Cosa è l'autenticazione?"    -> "cosa-e-l-autenticazione"
  "  Hello,  World!  "          -> "hello-world"
  "100% better"                 -> "100-better"
"""

import re
import sys
import unicodedata


def slugify(text: str) -> str:
    """Convert a title to a kebab-case ASCII slug."""
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: slugify.py <text>")
    print(slugify(sys.argv[1]))


if __name__ == "__main__":
    main()
