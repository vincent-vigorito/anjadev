"""Conferma umana locale esplicita; non esposta come tool MCP."""
import argparse
import re
from pathlib import Path

from anja import trust
from anja.persistence import check_revision, read_text, write_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('page', type=Path)
    parser.add_argument('--human', required=True, help='Identità dichiarata dell’operatore che ha svolto la review')
    parser.add_argument('--expected-revision', required=True, help='revision restituita da wiki.read')
    args = parser.parse_args()
    if not re.fullmatch(r'[\w.-]+', args.human):
        parser.error('invalid operator identity')
    text = read_text(args.page)
    expected = check_revision(args.page, text, args.expected_revision)
    updated = trust.append(text, 'human:' + args.human, origin='local-cli')
    print(write_text(args.page, updated, expected))


if __name__ == '__main__':
    main()
