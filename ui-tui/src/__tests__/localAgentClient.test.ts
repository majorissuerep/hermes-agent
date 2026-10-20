import { once } from 'node:events'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import type { ServerRequest } from '@hermes/shared/json-rpc-channel'
import { afterEach, expect, it, vi } from 'vitest'

import { LocalAgentClient } from '../localAgentClient.js'

const homes: string[] = []
const clients: LocalAgentClient[] = []

afterEach(() => {
  for (const client of clients.splice(0)) {
    client.kill('test cleanup')
  }

  for (const home of homes.splice(0)) {
    rmSync(home, { recursive: true, force: true })
  }

  vi.unstubAllEnvs()
})

function runtime() {
  const home = mkdtempSync(join(tmpdir(), 'hermes-local-stdio-'))
  homes.push(home)
  mkdirSync(join(home, 'tui_runtime'))
  writeFileSync(join(home, 'tui_runtime', '__init__.py'), '')
  writeFileSync(
    join(home, 'tui_runtime', '__main__.py'),
    `
import json, os, sys
def send(value):
    print(json.dumps(value), flush=True)
send({"jsonrpc":"2.0", "method":"event", "params":{"type":"runtime.ready", "payload":{}}})
for line in sys.stdin:
    frame=json.loads(line)
    if frame.get("method")=="ask":
        send({"jsonrpc":"2.0", "id":"srq-fixture", "method":"clarify", "params":{"session_id":"owned", "questions":[]}})
    elif frame.get("id")=="srq-fixture":
        send({"jsonrpc":"2.0", "method":"event", "params":{"type":"notice", "payload":{"message":frame["result"]["answer"]}}})
    elif frame.get("method")=="exit":
        sys.exit(7)
    else:
        send({"jsonrpc":"2.0", "id":frame["id"], "result":{"pid":os.getpid(), "params":frame.get("params")}})
`
  )
  vi.stubEnv('HERMES_PYTHON_SRC_ROOT', home)
  vi.stubEnv('HERMES_PYTHON', 'python3')
  vi.stubEnv('HOME', home)
  vi.stubEnv('HERMES_HOME', home)
  const client = new LocalAgentClient()
  clients.push(client)

  return client
}

it('owns a real stdio child and answers its blocking question on the same channel', async () => {
  const client = runtime()
  const ready = once(client, 'event')
  client.start()
  client.drain()
  expect((await ready)[0].type).toBe('runtime.ready')
  const echo = await client.request<{ pid: number; params: unknown }>('echo', { nonce: 'retained' })
  expect(echo.pid).not.toBe(process.pid)
  expect(echo.params).toEqual({ nonce: 'retained' })
  const question = once(client, 'request')
  const pending = client.request('ask').catch(() => undefined)
  const [request] = (await question) as [ServerRequest]
  const answered = once(client, 'event')
  request.respond({ answer: 'keyboard selection' })
  expect((await answered)[0].payload.message).toBe('keyboard selection')
  const exited = once(client, 'exit')
  client.kill()
  await exited
  await pending
  await expect(client.request('echo')).rejects.toThrow('closed')
})

it('rejects in-flight work when the owned child exits and never silently starts another', async () => {
  const client = runtime()
  client.drain()
  await client.request('echo')
  const exited = once(client, 'exit')
  await expect(client.request('exit')).rejects.toThrow('exited')
  expect((await exited)[0]).toBe(7)
  client.start()
  await expect(client.request('echo')).rejects.toThrow('closed')
})
