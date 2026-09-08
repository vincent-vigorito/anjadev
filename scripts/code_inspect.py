"""Read-only source facts. Syntax and declarations are not behavioral proofs."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import index_policy
from search_evidence import InvalidEvidence, source_snapshot

DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
OPERATIONS = (ast.Return, ast.Assert, ast.Call, ast.Compare, ast.BinOp, ast.Assign, ast.AnnAssign,
              ast.Raise, ast.Await, ast.Yield)
DECLARATION = re.compile(r'Implemented by:\s*([^\s:]+\.py):([\w.]+)\s*')


def span(node):
    return {'line_start': node.lineno, 'line_end': node.end_lineno or node.lineno}


class SyntaxFacts(ast.NodeVisitor):
    def __init__(self):
        self.owner = []
        self.symbols, self.imports, self.operations = [], [], []

    def generic_visit(self, node):
        definition = isinstance(node, DEFINITIONS)
        if definition:
            self.owner.append(node.name)
            item = dict(name='.'.join(self.owner), kind=type(node).__name__, **span(node))
            if node.decorator_list:
                item['line_start'] = min(item['line_start'], *(n.lineno for n in node.decorator_list))
            self.symbols.append(item)
        if not self.owner and isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    self.symbols.append(dict(name=target.id, kind=type(node).__name__, **span(node)))
        owner = '.'.join(self.owner) or '<module>'
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                self.imports.append(dict(type='imports', owner=owner, module=getattr(node, 'module', None),
                                         level=getattr(node, 'level', 0), name=alias.name, alias=alias.asname,
                                         provenance='python_ast', resolution='not_resolved', **span(node)))
        if isinstance(node, OPERATIONS):
            self.operations.append(dict(kind=type(node).__name__, owner=owner, provenance='python_ast', **span(node)))
        super().generic_visit(node)
        if definition:
            self.owner.pop()


def python_facts(text):
    facts = SyntaxFacts()
    facts.visit(ast.parse(text))
    return facts


def snapshot(root, path, allowed):
    path = Path(path).as_posix()
    if path not in allowed:
        raise InvalidEvidence('path_not_admitted_by_code_policy')
    return source_snapshot(root, path)


def inspect_source(root, path, symbol=None, expected_revision=None, max_chars=6000, max_items=100):
    if (type(max_chars) is not int or not 0 <= max_chars <= 100000
            or type(max_items) is not int or not 1 <= max_items <= 500):
        return {'error': 'invalid inspection budget', 'code': 'invalid_inspect_options'}
    if not isinstance(path, str) or not path or Path(path).is_absolute() or '..' in Path(path).parts:
        return {'error': 'path must be project-relative', 'code': 'invalid_inspect_path'}
    path = Path(path).as_posix()
    if Path(path).suffix not in ('.py', '.pyi', '.md'):
        return {'error': 'inspection supports Python and Markdown declarations', 'code': 'unsupported_inspection'}
    root = Path(root).resolve()
    try:
        allowed = {p.relative_to(root).as_posix() for p in index_policy.discover(root, 'code')[0]}
        revision, lines = snapshot(root, path, allowed)
    except (OSError, ValueError):
        return {'error': 'source unavailable or excluded by code policy', 'code': 'source_unavailable'}
    if expected_revision is not None and expected_revision != revision:
        return {'error': 'source changed since the supplied revision', 'code': 'revision_conflict',
                'current_revision': revision}
    text = '\n'.join(lines)
    symbols, operations, relations = [], [], []
    start, end = 1, len(lines)
    parse_status = 'parsed'
    if Path(path).suffix in ('.py', '.pyi'):
        try:
            facts = python_facts(text)
        except (SyntaxError, RecursionError):
            return {'error': 'Python syntax could not be parsed by this interpreter', 'code': 'parse_error',
                    'path': path, 'source_revision': revision}
        symbols, operations, relations = facts.symbols, facts.operations, facts.imports
        if symbol is not None:
            selected = [s for s in symbols if s['name'] == symbol]
            if len(selected) != 1:
                return {'error': 'symbol absent or ambiguous', 'code': 'symbol_not_unique', 'matches': len(selected)}
            start, end = selected[0]['line_start'], selected[0]['line_end']
            symbols = selected
            binding = selected[0]['kind'] in ('Assign', 'AnnAssign')
            if binding:
                operations = [o for o in operations if o['owner'] == '<module>' and start <= o['line_start'] <= end]
            else:
                operations = [o for o in operations if o['owner'] == symbol]
            # Imports at module scope are context, not proof that this function uses them.
            relations = [r for r in relations if r['owner'] in ('<module>', symbol)]
    else:
        if symbol is not None:
            return {'error': 'symbol selection requires Python', 'code': 'unsupported_inspection'}
        parse_status = 'declarations_only'
        for number, line in enumerate(lines, 1):
            match = DECLARATION.fullmatch(line.strip())
            if not match:
                continue
            target_path, target_symbol = match.groups()
            relation = dict(type='implemented_by', provenance='explicit_markdown_declaration',
                            line_start=number, line_end=number, target_path=target_path,
                            target_symbol=target_symbol, resolution='unavailable', behavior_verified=False)
            try:
                target_revision, target_lines = snapshot(root, target_path, allowed)
                targets = [s for s in python_facts('\n'.join(target_lines)).symbols if s['name'] == target_symbol]
                if len(targets) == 1:
                    relation.update(resolution='symbol_exists', target_revision=target_revision,
                                    target_span={k: targets[0][k] for k in ('line_start', 'line_end')})
                else:
                    relation['resolution'] = 'symbol_absent_or_ambiguous'
            except (OSError, ValueError, SyntaxError, RecursionError):
                pass
            relations.append(relation)
            if len(relations) > max_items:
                break
    remaining = max_items
    counts = {}
    output = {}
    for name, items in [('symbols', symbols), ('operations', operations), ('relations', relations)]:
        output[name] = items[:remaining]
        remaining -= len(output[name])
        counts[name] = len(items)
    excerpt = '\n'.join(lines[start - 1:end])
    return dict(path=path, source_revision=revision, revision_algorithm='sha256', freshness='verified_at_read',
                parser=parse_status, scope_span={'line_start': start, 'line_end': end} if end >= start else None,
                source=excerpt[:max_chars], source_truncated=len(excerpt) > max_chars,
                max_chars=max_chars, max_items=max_items, facts_truncated=sum(counts.values()) > max_items,
                **output, behavioral_claims='not_assessed', tests_executed=False,
                limits=['AST facts describe syntax, including unreachable code and unexecuted assertions.',
                        'Imports are declarations; module resolution, dispatch and call targets are not proven.',
                        'No returned operation proves or disproves a natural-language behavioral claim.',
                        'A declared implemented_by edge verifies at most symbol existence, not implementation correctness.',
                        'Snapshots are per file, not an atomic repository snapshot. Other languages are not analyzed.'])
