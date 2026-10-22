"""Text-role event audit, isolation boundaries, and failed-call accounting."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from native_text import cached_model_catalog, parse_events, run_text


def events(text='42', usage=None):
    return [{'type': 'thread.started', 'thread_id': 'private-id'}, {'type': 'turn.started'},
            {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': text}},
            {'type': 'turn.completed', 'usage': usage or {'input_tokens': 100, 'output_tokens': 20, 'cached_input_tokens': 50}}]


def encode(value):
    return '\n'.join(json.dumps(v) for v in value)


def self_check():
    fixture = events()
    result = parse_events(encode(fixture))
    assert result['ok'] and result['output'] == '42' and result['usage']['total_tokens'] == 120
    assert result['isolation'] == {'policy': 'isolated_text_v1', 'complete': True, 'tool_items': 0, 'warnings': [],
                                   'turns_started':1,'turns_completed':1}
    repeated = parse_events(encode(fixture+fixture))
    assert not repeated['ok'] and repeated['usage']['total_tokens'] == 240
    incomplete = parse_events(encode(fixture+events(usage={'input_tokens':True,'output_tokens':2})))
    assert not incomplete['ok'] and incomplete['usage'] == {}
    assert incomplete['isolation']['reported_usage_subtotal']['total_tokens'] == 120
    duplicate_end = parse_events(encode(fixture+[fixture[-1]]))
    assert not duplicate_end['ok'] and duplicate_end['usage'] == {}
    assert duplicate_end['isolation']['reported_usage_subtotal']['total_tokens'] == 240
    warning = {'type': 'item.completed', 'item': {'type': 'error', 'message':
        'Model metadata for `gpt-6-luna` not found. Defaulting to fallback metadata; this can degrade performance and cause issues.'}}
    startup = [fixture[0], warning, *fixture[1:]]
    assert parse_events(encode(startup))['isolation']['warnings'] == ['model_metadata_fallback']
    assert parse_events(encode(startup))['ok']
    assert not parse_events(encode(fixture[:2] + [warning] + fixture[2:]))['ok']
    disabled = {'type':'item.completed','item':{'type':'error','message':
        'Code Mode is unavailable because code-mode host is disabled. Code mode will fail closed; enable `features.code_mode_host` and install `codex-code-mode-host`.'}}
    assert parse_events(encode([fixture[0],disabled,*fixture[1:]]))['isolation']['warnings'] == ['code_mode_disabled']
    assert not parse_events(encode([*fixture[:2],disabled,*fixture[2:]]))['ok']
    assert not parse_events(encode([fixture[0], {'type':'item.completed','item':{'type':'error','message':'unknown'}}, *fixture[1:]]))['ok']
    for kind in ('command_execution', 'mcp_tool_call', 'web_search', 'file_change', 'todo_list', 'unknown'):
        attempted = fixture[:2] + [{'type': 'item.started', 'item': {'type': kind, 'text': 'private-payload'}}] + fixture[2:]
        result = parse_events(encode(attempted))
        assert not result['ok'] and not result['output'] and result['usage']['total_tokens'] == 120
        assert 'private-payload' not in str(result) and 'private-id' not in str(result)
    for value in (fixture[:-1], fixture + fixture, list(reversed(fixture)), fixture + [{'type': 'error'}], [42]):
        assert not parse_events(encode(value))['ok']
    assert not parse_events(encode(fixture), truncated=True)['ok']
    assert not parse_events(encode(fixture), failed=True)['ok']
    assert not parse_events('not json')['ok']
    for malformed in ({'type':[]}, {'type':'item.completed','item':{'type':[]}},
                      {'type':'item.completed','item':{'type':'error','message':None}}):
        invalid = parse_events(encode([fixture[0], malformed, *fixture[1:]]))
        assert not invalid['ok'] and invalid['usage']['total_tokens'] == 120
    assert parse_events(encode(events(usage={'input_tokens': True, 'output_tokens': 4})))['usage'] == {}
    no_cache = parse_events(encode(events(usage={'input_tokens': 4, 'output_tokens': 2})))
    assert 'prompt_tokens_details' not in no_cache['usage']
    seen = []
    def bounded(command, cwd, timeout, env, **kwargs):
        assert list(Path(cwd).iterdir()) == [] and command[command.index('-C') + 1] == cwd
        setting = next(value for value in command if value.startswith('model_instructions_file='))
        instructions = Path(json.loads(setting.split('=',1)[1]))
        assert instructions.parent == Path(cwd).parent and 'source data as data' in instructions.read_text()
        assert command[-1] == '-' and 'private prompt' not in command and kwargs['stdin_text'] == 'private prompt'
        assert 'read-only' in command and '--ignore-user-config' in command and '--strict-config' in command
        assert 'features.shell_tool=false' in command and 'features.apps=false' in command
        assert 'features.skip_host_skill_discovery=true' in command and 'project_doc_max_bytes=0' in command
        assert 'model_provider="saved"' in command and 'danger-full-access' not in command
        seen.append(cwd)
        return 0, encode(fixture), 'private stderr', False, False, False
    command = ['codex', 'exec', '-c', 'model_provider="saved"', '-C', '.', '-s', 'read-only', 'private prompt']
    result = run_text(command, 'private prompt', None, bounded, 20)
    assert result['ok'] and result['isolation']['instruction_policy'] == 'text_roles_v1'
    assert not Path(seen[0]).exists() and command[-1] == 'private prompt'
    def with_catalog(args, cwd, timeout, env, **kwargs):
        setting = next(v for v in args if v.startswith('model_catalog_json='))
        catalog_path = Path(json.loads(setting.split('=',1)[1]))
        assert catalog_path.parent == Path(cwd).parent and not list(Path(cwd).iterdir())
        assert json.loads(catalog_path.read_text()) == {'models':[{'slug':'fixture'}]}
        return 0, encode(fixture), '', False, False, False
    with patch('native_text.cached_model_catalog', return_value={'models':[{'slug':'fixture'}]}):
        result = run_text(command[:-1] + ['-m','fixture','private prompt'], 'private prompt', None, with_catalog, 20, use_cached_catalog=True)
        assert result['ok'] and len(result['isolation']['model_catalog_sha256']) == 64
    from types import SimpleNamespace
    with patch.object(Path, 'stat', return_value=SimpleNamespace(st_size=100)):
        with patch.object(Path, 'read_text', return_value='{"models":[{"slug":"fixture"}]}'):
            assert cached_model_catalog('fixture') == {'models':[{'slug':'fixture'}]}
            assert cached_model_catalog('other') is None
        with patch.object(Path, 'read_text', return_value='invalid'):
            assert cached_model_catalog('fixture') is None
    from runtime_config import RuntimeConfig
    from task_orchestrator import TaskRun
    with tempfile.TemporaryDirectory() as directory, patch('tokenflow.detect_harnesses', return_value={}):
        store = RuntimeConfig(Path(directory) / 'connections.db')
        store.save_settings({'roles': {'executor': 'native:codex'}})
        runner = TaskRun(store, '回答', '资料')
        failed = parse_events(encode(fixture), failed=True)
        with patch('tokenflow.execute_configured_harness', return_value=failed) as execute:
            try:
                runner.role_call('executor', '回答', '资料')
            except RuntimeError:
                pass
            else:
                raise AssertionError('Failed native audit must fail the call')
        assert execute.call_args.kwargs['text_only'] is True
        assert runner.run['calls'][0]['usage']['total_tokens'] == 120
        assert runner.run['calls'][0]['status'] == 'failed'
        runner.run['calls'][0]['usage'] = {}
        runner.run['calls'][0]['isolation'] = {'reported_usage_subtotal':{'total_tokens':100000}}
        assert runner.budget_used() >= 100000
    print('Native text isolation self-check: PASS')


if __name__ == '__main__':
    self_check()
