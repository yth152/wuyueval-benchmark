"""Durable jobs; one scheduler, user round-robin, explicit token reservations."""
import asyncio
import contextlib
import json
import time
import logging
from collections import Counter
import store as db

TERMINAL = {'complete', 'error', 'incomplete', 'stopped', 'interrupted'}


def select_next(rows, running, cfg, last_user):
    counts = Counter(job['user_id'] for job in running)
    used = sum(job['inflight'] for job in running)
    if len(running) >= cfg['max_parallel']: return None
    users = list(dict.fromkeys(row['user_id'] for row in rows))
    if last_user in users:
        index = users.index(last_user) + 1; users = users[index:] + users[:index]
    for user in users:
        if counts[user] >= cfg['per_user_parallel']: continue
        row = next(row for row in rows if row['user_id'] == user)
        if used + row['inflight'] <= cfg['inflight_token_budget']: return row
    return None


def reduce_event(turn, event):
    kind = event['type']
    if kind in ('content', 'reasoning'): turn['answer' if kind == 'content' else 'reasoning'] += event['text']
    elif kind == 'model_round': turn['phase'] = '正在检索与分析' if event['tools_available'] else '正在整理回答'
    elif kind == 'tool_plan': turn['answer'] = ''
    elif kind == 'tool_start': turn['toolCalls'].append({**event, 'status': 'running'})
    elif kind == 'tool_result':
        turn['toolCalls'] = [{**call, **event, 'status': 'complete' if event.get('ok') else 'error'} if call['call_id'] == event['call_id'] else call for call in turn['toolCalls']]
    elif kind == 'citations': turn.update(citations=event['sources'], searchMeta=event)
    elif kind == 'warning': turn['warning'] = (turn.get('warning', '') + ' ' + event['message']).strip()
    elif kind == 'usage': turn['_usage'] = event['usage']
    elif kind == 'error':
        turn.update(status='error', error=event['message'])
        if event.get('usage'): turn['_usage'] = event['usage']
    elif kind == 'done':
        turn.update(meta=event, status='complete' if event.get('complete') else 'incomplete')
        if event.get('usage'): turn['_usage'] = event['usage']
        if not event.get('complete'): turn['error'] = '本次生成达到长度上限或未完整结束，可继续提问。'
    return turn


def public_turn(row):
    turn = json.loads(row['turn'])
    turn.pop('_usage', None)
    turn.update(status=row['status'], revision=row['revision'])
    return turn


class Scheduler:
    def __init__(self, runner):
        self.runner = runner; self.tasks = {}; self.stopping = False; self.last_user = None; self.loop = None

    async def start(self):
        self.event_loop = asyncio.get_running_loop()
        for row in db.query("SELECT * FROM jobs WHERE status='running'"):
            turn = json.loads(row['turn']); turn.update(status='interrupted', error='服务重启使本次生成中断，已保留收到的内容。请重新提问。')
            db.execute("UPDATE jobs SET status='interrupted',turn=?,charged=reserved,finished=?,revision=revision+1 WHERE id=?", (json.dumps(turn, ensure_ascii=False), time.time(), row['id']))
        self.loop = asyncio.create_task(self.dispatch())

    async def stop(self):
        self.stopping = True
        if self.loop: self.loop.cancel()
        for task in list(self.tasks.values()): task.cancel()
        await asyncio.gather(*(list(self.tasks.values()) + ([self.loop] if self.loop else [])), return_exceptions=True)

    async def dispatch(self):
        while True:
            cfg = db.settings()
            rows = db.query("SELECT * FROM jobs WHERE status='queued' ORDER BY created,id")
            running = db.query("SELECT * FROM jobs WHERE status='running'")
            selected = select_next(rows, running, cfg, self.last_user)
            if selected:
                # No await between choosing and claiming. The process lock also
                # prevents another scheduler from owning these same rows.
                claimed = db.execute("UPDATE jobs SET status='running',started=?,revision=revision+1 WHERE id=? AND status='queued'", (time.time(), selected['id']))
                if claimed:
                    self.last_user = selected['user_id']
                    task = asyncio.create_task(self.run(selected, len({r['user_id'] for r in rows}) > 1))
                    self.tasks[selected['id']] = task
                    task.add_done_callback(lambda _, job_id=selected['id']: self.tasks.pop(job_id, None))
                continue
            await asyncio.sleep(.2)

    async def run(self, row, busy):
        turn = json.loads(row['turn']); turn['status'] = 'running'; last_save = 0
        cfg = db.settings(); output = min(turn['request']['max_tokens'], cfg['busy_output_tokens']) if busy else turn['request']['max_tokens']
        if output < turn['request']['max_tokens']:
            turn['warning'] = f'当前多人排队，本轮输出预算为 {output:,} tokens，可在回答后继续追问。'
        def flush():
            db.execute('UPDATE jobs SET turn=?,revision=revision+1 WHERE id=?', (json.dumps(turn, ensure_ascii=False), row['id']))
        try:
            async with asyncio.timeout(cfg['job_timeout']):
                async with contextlib.aclosing(self.runner(row, output)) as stream:
                    async for event in stream:
                        reduce_event(turn, event)
                        # Avoid a disk transaction for every individual token.
                        if event['type'] not in ('content', 'reasoning') or time.monotonic() - last_save >= .25:
                            flush(); last_save = time.monotonic()
            if turn['status'] == 'running': turn.update(status='incomplete', error='模型连接结束但未完成回答，请重试。')
        except asyncio.CancelledError:
            turn.update(status='interrupted' if self.stopping else 'stopped', error='服务重启，已保留生成内容。' if self.stopping else '已停止生成，已保留收到的内容。')
        except TimeoutError: turn.update(status='error', error='任务超时，已保留收到的内容。')
        except Exception: turn.update(status='error', error='生成服务暂不可用，请稍后再试或联系管理员。')
        usage = turn.pop('_usage', None) or turn.get('meta', {}).get('usage')
        # In-flight cancellation/timeout may omit final usage: reserve is charged
        # conservatively and explicitly marked, rather than inventing zero usage.
        exact = bool(usage and turn.get('meta', {}).get('usage') and not turn.get('meta', {}).get('usage_estimated') and turn['status'] in ('complete', 'incomplete') and usage.get('total_tokens') is not None)
        charged = int(usage['total_tokens']) if exact else row['reserved']
        turn['accounting'] = {'tokens': charged, 'estimated': not exact}
        db.execute('UPDATE jobs SET status=?,turn=?,charged=?,finished=?,revision=revision+1 WHERE id=?',
                   (turn['status'], json.dumps(turn, ensure_ascii=False), charged, time.time(), row['id']))
        db.execute('UPDATE conversations SET updated=? WHERE id=?', (time.time(), row['conversation_id']))
        if turn['status'] == 'complete':
            try:
                import memory
                await asyncio.to_thread(memory.after_completion, row)
            except Exception:
                # The lossless answer is already committed. Retry indexing from
                # the archive on the next question, without failing the answer.
                logging.getLogger(__name__).warning('Memory indexing deferred for job %s', row['id'])

    def cancel(self, row):
        # All state changes for cancellation run on the scheduler loop, avoiding
        # a stale queued-row race with dispatch and thread-unsafe task access.
        self.event_loop.call_soon_threadsafe(self._cancel, row['id'])

    def _cancel(self, job_id):
        row = db.query('SELECT * FROM jobs WHERE id=?', (job_id,), one=True)
        if not row: return
        if row['status'] == 'queued':
            turn = json.loads(row['turn']); turn.update(status='stopped', error='已取消排队。')
            db.execute("UPDATE jobs SET status='stopped',turn=?,charged=0,finished=?,revision=revision+1 WHERE id=? AND status='queued'", (json.dumps(turn, ensure_ascii=False), time.time(), row['id']))
        elif row['id'] in self.tasks:
            self.tasks[row['id']].cancel()
