"""PI-5B2.1 tool visibility and pre-launch failure regressions; no inference."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from personal_ai_orchestrator.pi5_tool import seed_pi5_socket_tool
from personal_ai_orchestrator.pi_dispatch_executor import should_pao_delegate_be_model_visible
from personal_ai_orchestrator.pi_runtime import (
    PI_ALLOWED_TOOLS,
    PiDelegationActivationError,
    PiRuntimeConfig,
    build_pi_json_argv,
    seed_pi_worktree_guard,
)
from personal_ai_orchestrator.safety_kernel import SafetyKernelStore, TaskState
from tests.test_pi5_execution_lifecycle import _enabled


@pytest.mark.parametrize('enabled,authority,task_request,expected', [
    (False, 'OWNER_INITIATED_EXECUTION', 'parent', False),
    (True, 'OWNER_INITIATED_EXECUTION', 'parent', True),
    (True, 'SUPERVISED_AUTO', 'parent', True),
    (True, 'DELEGATED_CHILD', 'child', False),
    (True, 'OWNER_INITIATED_EXECUTION', 'pi5-child-submit-retried', False),
    (True, 'UNKNOWN', 'parent', False),
])
def test_visibility_is_host_authority_not_extension_installation(
    tmp_path, enabled, authority, task_request, expected,
):
    tool = seed_pi5_socket_tool(tmp_path, socket_path=tmp_path/'broker.sock')
    config = PiRuntimeConfig(delegation_enabled=enabled)
    visible = should_pao_delegate_be_model_visible(config, authority, task_request)
    assert visible is expected
    argv = build_pi_json_argv(
        config=replace(config, delegation_enabled=visible), model_ref='minimax-cn/MiniMax-M3',
        intent='Even if prompted, a child cannot delegate', guard_path=Path('/host/guard.ts'),
        delegation_tool_path=tool,
    )
    tools = argv[argv.index('--tools')+1].split(',')
    assert tools == [*PI_ALLOWED_TOOLS, *(['pao_delegate'] if expected else [])]
    assert tools.count('pao_delegate') == int(expected)
    assert (str(tool) in argv) is expected
    assert '--no-extensions' in argv
    assert not {'bash', 'powershell', 'webfetch'}.intersection(tools)


@pytest.mark.parametrize('override', [
    ('--tools', 'read'), ('-t', 'read'), ('--tools=read',),
    ('--exclude-tools', 'pao_delegate'), ('--no-tools',), ('--no-builtin-tools',),
])
def test_conflicting_enabled_tool_overrides_are_typed_failures(override):
    with pytest.raises(PiDelegationActivationError) as error:
        build_pi_json_argv(config=PiRuntimeConfig(delegation_enabled=True, extra_args=override),
                           model_ref='minimax-cn/MiniMax-M3', intent='fixture',
                           guard_path=Path('/guard'), delegation_tool_path=Path('/tool'))
    assert error.value.code == 'PI_DELEGATION_ACTIVATION_UNAVAILABLE'
    assert error.value.reason_code == 'CONFLICTING_TOOL_CONFIGURATION'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['port', 'missing', 'tampered', 'wrong_socket', 'bad_argv'])
async def test_unresolved_activation_never_launches_worker_and_cleans_up(
    tmp_path, monkeypatch, failure,
):
    import personal_ai_orchestrator.pi_dispatch_executor as adapter

    executor, db = _enabled(tmp_path)
    roots = []
    original_seed = adapter.seed_pi5_socket_tool
    def seed(root, *, socket_path):
        roots.append(root)
        path = original_seed(root, socket_path=socket_path)
        if failure == 'missing':
            path.unlink()
        elif failure == 'tampered':
            path.write_text('export default function () {}')
        elif failure == 'wrong_socket':
            path = original_seed(root, socket_path=socket_path.with_name('foreign.sock'))
        return path
    monkeypatch.setattr(adapter, 'seed_pi5_socket_tool', seed)
    if failure == 'port':
        executor.delegation_child_port = None
    if failure == 'bad_argv':
        original_build = adapter.build_pi_json_argv
        def bad_build(**kwargs):
            return tuple(a.replace(',pao_delegate', '') for a in original_build(**kwargs))
        monkeypatch.setattr(adapter, 'build_pi_json_argv', bad_build)
    starts = []
    async def forbidden_start(*args, **kwargs):
        starts.append(True)
        raise AssertionError('must fail before worker spawn')
    monkeypatch.setattr(executor._supervisor, 'start', forbidden_start)
    await executor.execute_async('dispatch-pi')
    assert starts == []
    assert executor._delegation_brokers == {}
    assert all(not root.exists() for root in roots)
    store = SafetyKernelStore(db)
    try:
        diagnostics = [e for e in store.audit_events('task-pi')
                       if e['event_type'] == 'PI_DELEGATION_ACTIVATION_UNAVAILABLE']
        assert len(diagnostics) == 1
        assert set(diagnostics[0]['payload']) == {'reason_code'}
        assert store.connection.execute('SELECT COUNT(*) FROM runs').fetchone()[0] == 0
        assert store.get_task('task-pi').state is TaskState.BLOCKED
        assert store.get_workspace('task-pi').writer_token is None
    finally:
        store.close()


def test_installed_pi_0851_sdk_activation_without_inference(tmp_path):
    """Opt-in installed SDK contract: never invokes pi CLI, auth, prompt or a model."""
    root = os.environ.get('PAO_PI_SDK_ROOT')
    if root is None:
        pytest.skip('Set PAO_PI_SDK_ROOT for offline installed Pi 0.85.1 activation coverage')
    package = Path(root)
    assert json.loads((package/'package.json').read_text())['version'] == '0.85.1'
    node = shutil.which('node')
    assert node
    tool = seed_pi5_socket_tool(tmp_path/'trusted', socket_path=tmp_path/'unused.sock')
    guard = seed_pi_worktree_guard(tmp_path/'trusted')
    invocations = []
    for enabled in (False, True):
        invocations.append(build_pi_json_argv(
            config=PiRuntimeConfig(delegation_enabled=enabled),
            model_ref='minimax-cn/MiniMax-M3', intent='never submitted',
            guard_path=guard, delegation_tool_path=tool,
        )[1:])
    payload = tmp_path/'invocations.json'
    payload.write_text(json.dumps(invocations))
    runner = Path(__file__).parent/'fixtures/pi0851_tool_activation.mjs'
    env = {'PATH': os.environ['PATH']}
    result = subprocess.run(
        [node, str(runner), str(package), str(tmp_path), str(payload), str(tool)],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    report = json.loads(result.stdout)
    assert report == {
        'version': '0.85.1', 'model_calls': 0,
        'disabled': list(PI_ALLOWED_TOOLS),
        'enabled': [*PI_ALLOWED_TOOLS, 'pao_delegate'],
        'installed_only': list(PI_ALLOWED_TOOLS),
    }


def test_disabled_argv_matches_pre_fix_contract_byte_for_byte():
    argv = build_pi_json_argv(
        config=PiRuntimeConfig(pi_bin='/host/pi', extra_args=('--thinking', 'low')),
        model_ref='minimax-cn/MiniMax-M3', intent='ordinary task',
        guard_path=Path('/host/guard.ts'),
        delegation_tool_path=Path('/installed-but-unauthorized.ts'),
    )
    assert argv == (
        '/host/pi', '--mode', 'json', '--no-session', '--no-approve', '--no-extensions',
        '-e', '/host/guard.ts', '--no-skills', '--no-prompt-templates', '--no-context-files',
        '--tools', 'read,edit,write,grep,find,ls', '--model', 'minimax-cn/MiniMax-M3',
        '--thinking', 'low', '--', 'ordinary task',
    )


@pytest.mark.parametrize('field', [
    'executable', 'provider_credential', 'repo_path', 'worktree_path', 'verifier_command',
    'depth', 'shell_command', 'provider', 'model', 'runtime', 'execution_target_id',
])
def test_visibility_does_not_add_worker_authority_fields(field):
    from pydantic import ValidationError

    from personal_ai_orchestrator.pi5_contract import DelegationRequest

    with pytest.raises(ValidationError):
        DelegationRequest.model_validate({
            'tool_call_id': 'fixture-call', 'ordinal': 1, 'intent': 'review', 'reason': 'test',
            field: 'untrusted-choice',
        })
