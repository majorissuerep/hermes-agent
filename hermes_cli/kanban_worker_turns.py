"""Claim-bound local terminal task execution and immutable attempt results."""
import json
import sqlite3
from pathlib import Path
from contextlib import closing

def _record_worker_result(context, code, last_output):
    import os
    from hermes_cli.kanban_db_connect import connect_closing
    from hermes_cli import kanban_db as kb
    with connect_closing(Path(context['db'])) as conn, kb.write_txn(conn):
        if _bound_worker_matches(conn, context):
            # Closing a run clears its claim/PID and replaces metadata; keep the
            # result in the immutable attempt event stream instead.
            kb._append_event(conn, context['task_id'], 'worker_result',
                {'pid': os.getpid(), 'claim_lock': context['claim_lock'], 'exit_code': code,
                 'last_output': last_output}, run_id=context['run_id'])

def _bound_worker_matches(conn, context):
    import os
    row = conn.execute("SELECT payload FROM task_events WHERE task_id=? AND run_id=? AND kind='worker_bound' ORDER BY id DESC LIMIT 1",
        (context['task_id'], context['run_id'])).fetchone()
    bound = json.loads(row[0]) if row else {}
    return (bound.get('pid'), bound.get('claim_lock')) == (os.getpid(), context['claim_lock'])

def _bound_claim_is_live(context):
    """Retry proof for a managed worker: the frame's exact run/claim lease is live and unexpired,
    and THIS interpreter is the one ``bind_worker_context`` bound to it. The run's worker_pid is
    the dispatcher's spawned submitter here, so pid identity comes from ``worker_bound`` instead."""
    from agent.kanban_turn_recovery import worker_claim_is_live
    if not worker_claim_is_live(task_id=context['task_id'], db_path=context['db'], run_id=str(context['run_id']),
                                claim_lock=context['claim_lock'], verify_pid=False):
        return False
    try:
        with closing(sqlite3.connect(Path(context['db']).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            return _bound_worker_matches(conn, context)
    except sqlite3.Error:
        return False

def _run_task_turns(agent, frame, history, context):
    from agent.skill_commands import build_preloaded_skills_prompt
    skills, loaded, missing = build_preloaded_skills_prompt(context['skills'], task_id=agent.session_id)
    if missing and not loaded:
        raise ValueError('kanban_skills_unavailable')
    prompt = '\n\n'.join(p for p in (skills, context['context'], frame['text']) if p)
    result = agent.run_conversation(prompt, conversation_history=history)

    def recover(nudge):
        nonlocal result
        prior = result.get('messages') if isinstance(result, dict) else None
        result = agent.run_conversation(nudge, conversation_history=prior or
                                        agent._session_db.get_messages_as_conversation(agent.session_id))
    # Same in-place retry as the one-shot CLI worker (agent/kanban_turn_recovery.py), but the
    # carrier is this frame's bound claim, never the owner process's environment.
    from agent.kanban_turn_recovery import recover_failed_kanban_turns
    recover_failed_kanban_turns(recover, lambda: result, task_id=context['task_id'],
                                claim_check=lambda: _bound_claim_is_live(context))
    from hermes_cli.turn_exit import turn_exit_code
    # Only a settled turn may continue the goal loop: an exhausted or denied recovery reaches
    # the honest exit path without re-entering the model under an unprovable claim.
    if not context['goal_mode'] or turn_exit_code(result, kanban_worker=True) != 0:
        return result
    from hermes_cli.goals import run_kanban_goal_loop, DEFAULT_MAX_TURNS
    from hermes_cli import kanban_db as kb
    from hermes_cli.kanban_db_connect import connect_closing
    def status():
        with connect_closing() as conn:
            return kb.goal_run_status(conn, context['task_id'], context['run_id'])
    def block(reason):
        with connect_closing() as conn:
            kb.block_task(conn, context['task_id'], reason=reason, expected_run_id=context['run_id'])
    def turn(prompt):
        nonlocal result
        result = agent.run_conversation(prompt, conversation_history=agent._session_db.get_messages_as_conversation(agent.session_id))
        return result.get('final_response', '')
    run_kanban_goal_loop(task_id=context['task_id'], goal_text=context['goal_text'], run_turn=turn,
        task_status_fn=status, block_fn=block, max_turns=context['goal_max_turns'] or DEFAULT_MAX_TURNS,
        first_response=result.get('final_response', ''))
    return result
