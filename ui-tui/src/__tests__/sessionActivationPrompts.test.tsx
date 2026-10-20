import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { PassThrough } from 'node:stream'

import { renderSync, Text } from '@hermes/ink'
import { JsonRpcRequestChannel } from '@hermes/shared/json-rpc-channel'
import React from 'react'
import { expect, it, vi } from 'vitest'

import { createServerRequestHandler } from '../app/createServerRequestHandler.js'
import { $overlayState } from '../app/overlayStore.js'
import { turnController } from '../app/turnController.js'
import { $uiState, resetUiState } from '../app/uiStore.js'
import { useSessionLifecycle } from '../app/useSessionLifecycle.js'
import { LocalAgentClient } from '../localAgentClient.js'

const noop = () => {}

// Native session payloads replay still-open private requests through the actual channel.
const snapshotA = {
  session_id: 'a',
  session_key: 'a',
  running: true,
  status: 'waiting',
  messages: [],
  info: { model: 'test', tools: {}, skills: {}, stored_session_id: 'a' },
  open_requests: [
    {
      id: 'p-a',
      method: 'approval',
      params: { session_id: 'a', description: 'review command', command: 'sentinel', choices: ['once', 'deny'] }
    }
  ]
}

const snapshotB = {
  ...snapshotA,
  session_id: 'b',
  session_key: 'b',
  running: false,
  open_requests: [],
  info: { ...snapshotA.info, stored_session_id: 'b' }
}

function mount() {
  const home = mkdtempSync(join(tmpdir(), 'ink-activate-prompts-'))
  vi.stubEnv('HERMES_HOME', home)
  resetUiState()

  const gw = new LocalAgentClient()
  const handle = createServerRequestHandler({ ringPromptBell: noop, setStatus: noop })
  const channel = new JsonRpcRequestChannel()
  channel.onRequest(request => handle(request))
  channel.attach({
    send(text) {
      const frame = JSON.parse(text)

      if (!frame.method) {
        return
      }

      const result = frame.params?.session_id === 'b' ? snapshotB : snapshotA
      queueMicrotask(() => channel.handleFrame(JSON.stringify({ jsonrpc: '2.0', id: frame.id, result })))
    }
  })
  vi.spyOn(gw, 'request').mockImplementation((method, params) => channel.request(method, params))

  let lifecycle!: ReturnType<typeof useSessionLifecycle>

  function Harness() {
    lifecycle = useSessionLifecycle({
      colsRef: { current: 80 },
      composerActions: { setComposerTokens: noop },
      gw,
      rpc: async () => null,
      scrollRef: { current: null },
      panel: noop,
      sys: noop,
      setHistoryItems: noop,
      setLastUserMsg: noop,
      setSessionStartedAt: noop,
      setStickyPrompt: noop,
      setVoiceProcessing: noop,
      setVoiceRecording: noop
    } as any)

    return <Text>probe</Text>
  }

  const instance = renderSync(<Harness />, {
    stdin: new PassThrough() as any,
    stdout: Object.assign(new PassThrough(), { columns: 80, rows: 20, isTTY: false }) as any,
    stderr: new PassThrough() as any,
    patchConsole: false
  })

  return {
    get lifecycle() {
      return lifecycle
    },
    cleanup() {
      instance.unmount()
      turnController.fullReset()
      resetUiState()
      vi.unstubAllEnvs()
      rmSync(home, { recursive: true, force: true })
    }
  }
}

it('activating a live session hydrates its pending approval exactly like resume does', async () => {
  const h = mount()

  try {
    await new Promise(resolve => setImmediate(resolve))
    h.lifecycle.resumeById('a')
    await expect.poll(() => $overlayState.get().approval?.requestId).toBe('p-a')

    h.lifecycle.activateLiveSession('b')
    await expect.poll(() => $uiState.get().sid).toBe('b')
    expect($overlayState.get().approval).toBeNull()

    h.lifecycle.activateLiveSession('a')
    await expect.poll(() => $uiState.get().sid).toBe('a')
    await expect.poll(() => $overlayState.get().approval?.requestId).toBe('p-a')
  } finally {
    h.cleanup()
  }
})
