"""Verifiche revisionate; i campi di audit non verificano se stessi."""
import hashlib
import json
import re
from datetime import datetime, timezone

AUDIT_FIELDS = {'verified', 'generated', 'updated'}


def _parts(text):
    match = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)', text, re.S)
    if not match:
        return None, text
    return match, text[match.end():]


def _without_fields(block, fields):
    lines = []
    skip = False
    for line in block.splitlines():
        key = re.match(r'^([\w-]+):', line)
        if key:
            skip = key.group(1) in fields
        elif line and not line[0].isspace():
            skip = False
        if not skip:
            lines.append(line)
    return '\n'.join(lines)


def content_revision(text):
    match, body = _parts(text)
    content = (_without_fields(match.group(1), AUDIT_FIELDS) + '\n---\n' + body) if match else body
    return 'sha256:' + hashlib.sha256(content.encode()).hexdigest()


def events(text):
    match, _ = _parts(text)
    if not match:
        return []
    field = re.search(r'^verified:([^\n]*(?:\n[ \t]+[^\n]*)*)', match.group(1), re.M)
    if not field or field.group(1).strip() in ('', '[]'):
        return []
    value = field.group(1).strip()
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else [parsed]
    except ValueError:
        # Conserva gli eventi YAML legacy senza attribuire loro una revisione.
        return [{'legacy': value}]


def status(text):
    rev = content_revision(text)
    history = events(text)
    current = [e for e in history if isinstance(e, dict) and e.get('content_revision') == rev]
    if current:
        tier = 'human-reviewed' if any(str(e.get('by', '')).startswith('human:') and e.get('origin') == 'local-cli' for e in current) else 'machine-confirmed'
    elif any(isinstance(e, dict) and e.get('content_revision') for e in history):
        tier = 'stale'
    else:
        tier = 'legacy' if history else 'unverified'
    return {'trust_tier': tier, 'content_revision': rev, 'verifications': len(history),
            'current_verifications': len(current)}


def append(text, by, origin='mcp'):
    match, body = _parts(text)
    if not match:
        raise ValueError('page has no frontmatter')
    if by.startswith('human:') and origin != 'local-cli':
        raise ValueError('human verification requires the local operator CLI')
    history = events(text)
    history.append({'by': by, 'at': datetime.now(timezone.utc).isoformat(),
                    'content_revision': content_revision(text), 'origin': origin})
    block = _without_fields(match.group(1), {'verified'})
    return '---\n' + block + '\nverified: ' + json.dumps(history, ensure_ascii=False) + '\n---\n' + body
