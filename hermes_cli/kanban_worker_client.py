"""One claimed card executes in this bounded, terminal-owned agent process."""
try:
    import hermes_bootstrap
except ModuleNotFoundError as exc:
    if exc.name != "hermes_bootstrap":
        raise

import json
import os
from pathlib import Path
import sys


def _claim_context():
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing
    from hermes_cli.kanban_db_dispatch import _process_fingerprint, UNVERIFIED_WORKER_FINGERPRINT
    from hermes_cli.profiles import resolve_profile_env
    from hermes_constants import get_hermes_home

    task_id = os.environ['HERMES_KANBAN_TASK']
    run_id = int(os.environ['HERMES_KANBAN_RUN_ID'])
    claim = os.environ['HERMES_KANBAN_CLAIM_LOCK']
    path = Path(os.environ['HERMES_KANBAN_DB']).resolve(strict=True)
    with connect_closing(path) as conn, kb.write_txn(conn):
        task = kb.get_task(conn, task_id)
        run = conn.execute('SELECT * FROM task_runs WHERE id=?', (run_id,)).fetchone()
        if (task is None or run is None or task.status != 'running' or run['status'] != 'running'
                or task.current_run_id != run_id or run['task_id'] != task.id
                or task.claim_lock != claim or run['claim_lock'] != claim):
            raise RuntimeError('stale_kanban_claim')
        if Path(resolve_profile_env(task.assignee)).resolve() != get_hermes_home().resolve():
            raise RuntimeError('kanban_profile_mismatch')
        bound = conn.execute("SELECT payload FROM task_events WHERE task_id=? AND run_id=? AND kind='worker_bound' ORDER BY id DESC LIMIT 1", (task_id, run_id)).fetchone()
        if bound:
            from hermes_cli.kanban_db_dispatch import _worker_alive
            prior = json.loads(bound[0])
            if _worker_alive(prior.get('pid'), prior.get('started_at')):
                raise RuntimeError('kanban_attempt_already_running')
        kb._append_event(conn, task.id, 'worker_bound', {'pid': os.getpid(), 'claim_lock': claim,
            'started_at': _process_fingerprint(os.getpid()) or UNVERIFIED_WORKER_FINGERPRINT}, run_id=run_id)
        context = {'task_id': task_id, 'run_id': run_id, 'claim_lock': claim, 'db': str(path),
            'context': kb.build_worker_context(conn, task.id), 'skills': list(task.skills or ()),
            'goal_mode': task.goal_mode, 'goal_max_turns': task.goal_max_turns,
            'goal_text': '\n\n'.join(part for part in (task.title, task.body) if part)}
        if kb._retry_status_for_run(conn, task.id, run_id) == 'review':
            context['skills'] = list(dict.fromkeys([*context['skills'], 'sdlc-review']))
    return task, context


def run_claim() -> int:
    from argparse import Namespace
    from hermes_cli.vault_gate import gate_startup
    from hermes_cli.oneshot import _run_agent
    from hermes_cli.kanban_worker_turns import _run_task_turns, _record_worker_result
    from hermes_cli.turn_exit import credential_failure_flags, turn_exit_code
    from hermes_state.runtime_ownership import ProfileOwnership
    from hermes_constants import get_hermes_home
    gate_startup(Namespace(command='chat'))
    owner = ProfileOwnership()
    owner.reserve([get_hermes_home()])
    context = None
    code, output = 1, ''
    try:
        task, context = _claim_context()
        os.chdir(task.workspace_path)
        def turns(agent, prompt, history):
            return _run_task_turns(agent, {'text': prompt}, history, context)
        output, result = _run_agent(f'Work kanban task {task.id}', model=task.model_override,
            provider=task.provider_override, reasoning=task.reasoning_effort, conversation_runner=turns)
        code = turn_exit_code(result, kanban_worker=True)
        if output:
            print(output)
        return code
    except Exception as exc:  # health: allow BLE001 -- bounded worker reports arbitrary provider failure through its exit/result protocol
        code = turn_exit_code(None, kanban_worker=True, **credential_failure_flags(exc))
        print(f'Kanban worker failed: {exc}', file=sys.stderr)
        return code
    finally:
        try:
            if context is not None:
                _record_worker_result(context, code, output[-500:])
        finally:
            owner.close()


def main():
    from hermes_cli.quiet_single_query import KANBAN_WORKER_EXIT_TRAILER
    code = 1
    try:
        code = run_claim()
        return code
    finally:
        print(f'\n{KANBAN_WORKER_EXIT_TRAILER}{code}', file=sys.stderr, flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
