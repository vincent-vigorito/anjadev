"""Build a relocatable runtime archive from explicit plugin roots, including local edits."""
import argparse
import hashlib
import json
import tarfile
from pathlib import Path

ROOTS = ('.claude-plugin', '.codex-plugin', '.mcp.codex.json', '.opencode/plugin',
         'agents', 'commands', 'hooks', 'scripts', 'skills', 'templates', 'plugins',
         'README.md', 'SCHEMA.md', 'SECURITY.md', 'CHANGELOG.md', 'LICENSE')


def build(root, output):
    root = Path(root).resolve()
    files = []
    for name in ROOTS:
        base = root / name
        if not base.exists():
            raise ValueError('missing package component: ' + name)
        for path in [base, *sorted(base.rglob('*'))] if base.is_dir() else [base]:
            rel = path.relative_to(root)
            if '__pycache__' in rel.parts or path.suffix in ('.pyc', '.pyo'):
                continue
            if not path.resolve().is_relative_to(root):
                raise ValueError('package link escapes root: ' + str(rel))
            if path.name.endswith('.env') or path.name.startswith('.secrets'):
                raise ValueError('credential file in runtime package: ' + str(rel))
            files.append(path)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    inventory = {}
    with tarfile.open(output, 'w:gz', dereference=False) as archive:
        for path in sorted(set(files)):
            rel = str(path.relative_to(root))
            archive.add(path, arcname='anja/' + rel, recursive=False)
            if path.is_symlink():
                inventory[rel] = {'link': str(path.readlink())}
            elif path.is_file():
                inventory[rel] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    return {'version': json.loads((root / '.codex-plugin/plugin.json').read_text())['version'],
            'archive_sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'files': inventory}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    report = build(Path(__file__).resolve().parents[1], args.output)
    args.output.with_suffix(args.output.suffix + '.manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'version': report['version'], 'files': len(report['files']), 'sha256': report['archive_sha256']}))
