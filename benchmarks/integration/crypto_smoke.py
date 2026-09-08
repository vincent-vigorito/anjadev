"""Offline MCP acceptance on a temporary copy of crypto source; never execute trading code."""
import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(plugin, source, python):
    with tempfile.TemporaryDirectory(prefix='anja-crypto-v031-') as temporary:
        root = Path(temporary) / 'crypto'
        root.mkdir()
        candidates = [source / 'backtest_v3.py']
        for name in ('framework', 'strategies', 'tests'):
            candidates.extend((source / name).rglob('*.py'))
        hashes = {}
        for path in sorted(set(candidates)):
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(source):
                continue
            rel = path.relative_to(source)
            if '__pycache__' in rel.parts or path.stat().st_size > 500000:
                continue
            hashes[str(rel)] = digest(path)
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
            assert digest(target) == hashes[str(rel)]
        assert 'framework/engine_v3.py' in hashes and 'backtest_v3.py' in hashes
        home = Path(temporary) / 'home'
        home.mkdir()
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(home),
               'ANJA_ROOT': str(root), 'ANJA_SCOPE': 'project', 'ANJA_EMBED_PROVIDER': 'mock',
               'ANJA_WIKI_EMBED': '0', 'ANJA_AUTO_SUMMARY': '0', 'PYTHONDONTWRITEBYTECODE': '1'}
        init = subprocess.run([python, str(plugin/'scripts/init_project.py'), '--type', 'dev', '--mode', 'cold',
                               '--target', str(root/'.anjawiki'), '--name', 'crypto-test'],
                              env=env, cwd=root, capture_output=True, text=True, timeout=60)
        assert init.returncode == 0, init.stderr
        checks = []

        def rpc(name, arguments):
            requests = [{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},
                        {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':name,'arguments':arguments}}]
            result = subprocess.run([python, str(plugin/'scripts/mcp_memory_server.py')],
                                    input='\n'.join(map(json.dumps, requests))+'\n', text=True,
                                    capture_output=True, cwd=root, env=env, timeout=180)
            assert result.returncode == 0, result.stderr
            messages = {m['id']:m for line in result.stdout.splitlines() if line.startswith('{')
                        for m in [json.loads(line)] if 'id' in m}
            assert messages[1]['result']['serverInfo']['version'] == '0.31.0'
            response = messages[2]
            assert 'error' not in response, response
            return json.loads(response['result']['content'][0]['text'])

        for query, expected in [('BacktestEngineV3','framework/engine_v3.py'), ('_parse_dt','backtest_v3.py')]:
            result = rpc('code.search', {'query':query,'smart_level':0,'limit':10})
            assert expected in json.dumps(result), result
            checks.append('lexical:' + query)
        inspected = rpc('code.inspect', {'path':'framework/engine_v3.py','symbol':'BacktestEngineV3._track_trade'})
        assert 'error' not in inspected, inspected
        revision = inspected['source_revision']
        checks.append('inspect:BacktestEngineV3._track_trade')
        indexed = rpc('code.reindex', {'force':True})
        assert indexed.get('status') == 'ready', indexed
        checks.append('index:ready')
        vector = rpc('code.search', {'query':'BacktestEngineV3','smart_level':2,'limit':10})
        assert vector.get('level') == 2 and vector.get('method') == 'vector_search', vector
        assert 'error' not in vector and vector.get('results') and '_fallback_reason' not in vector, vector
        checks.append('vector:mock-operational')
        target = root / 'framework/engine_v3.py'
        target.write_text(target.read_text()+'\n# ANJA_CRYPTO_FRESHNESS_SENTINEL\n')
        stale = rpc('code.inspect', {'path':'framework/engine_v3.py','expected_revision':revision})
        assert stale.get('code') == 'revision_conflict', stale
        fresh = rpc('code.search', {'query':'ANJA_CRYPTO_FRESHNESS_SENTINEL','smart_level':0})
        assert 'framework/engine_v3.py' in json.dumps(fresh), fresh
        assert rpc('code.reindex', {}).get('status') == 'ready'
        checks.append('modified-copy:revision-conflict-and-refresh')
        for rel, expected_hash in hashes.items():
            assert digest(source / rel) == expected_hash, 'original changed: ' + rel
        return {'plugin_version':'0.31.0','source_files':len(hashes),'source_bytes':sum((source/p).stat().st_size for p in hashes),
                'source_hashes':hashes,'checks':checks,'original_sources_unchanged':True,
                'provider':'mock','remote_requests':0,'trading_code_executed':False,
                'limits':['No semantic quality measurement with mock', 'No real host install/restart',
                          'No application backtests or trading tests executed', 'Source subset only; no private project state copied']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plugin', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--python', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = run(args.plugin.resolve(), args.source.resolve(), args.python)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k != 'source_hashes'}, indent=2))
