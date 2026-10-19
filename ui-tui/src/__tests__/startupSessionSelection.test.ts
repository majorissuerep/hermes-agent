import { beforeEach, describe, expect, it, vi } from 'vitest'

import { createGatewayEventHandler } from '../app/createGatewayEventHandler.js'
import { resetOverlayState } from '../app/overlayStore.js'
import { resetTurnState } from '../app/turnStore.js'
import { getUiState, patchUiState, resetUiState } from '../app/uiStore.js'

const ref = <T>(current: T) => ({ current })

const buildCtx = (request: ReturnType<typeof vi.fn>) =>
  ({
    composer: { dequeue: () => undefined, queueEditRef: ref(null), sendQueued: vi.fn(), setInput: vi.fn() },
    gateway: { gw: { request }, rpc: vi.fn(async () => null) },
    session: {
      STARTUP_RESUME_ID: '',
      hasSessionSelection: () => false,
      colsRef: ref(80),
      newSession: vi.fn(),
      resetSession: vi.fn(),
      resumeById: vi.fn(),
      setCatalog: vi.fn()
    },
    submission: { submitRef: { current: vi.fn() } },
    system: { bellOnComplete: false, sys: vi.fn() },
    transcript: { appendMessage: vi.fn(), panel: vi.fn(), setHistoryItems: vi.fn() },
    voice: { setProcessing: vi.fn(), setRecording: vi.fn(), setVoiceEnabled: vi.fn() }
  }) as any

describe('startup session selection ownership', () => {
  beforeEach(() => {
    resetOverlayState()
    resetUiState()
    resetTurnState()
  })
  it.each(['before-ready', 'config-pending', 'recent-pending', 'config-error'])(
    'preserves an explicit resume at %s instead of selecting a startup session',
    async phase => {
      let selected = phase === 'before-ready'
      let resolveConfig!: (value: any) => void
      let rejectConfig!: (reason: Error) => void
      let resolveRecent!: (value: any) => void

      const config = new Promise((resolve, reject) => {
        resolveConfig = resolve
        rejectConfig = reject
      })

      const recent = new Promise(resolve => {
        resolveRecent = resolve
      })

      const ctx = buildCtx(vi.fn(async () => null))
      ctx.session.hasSessionSelection = () => selected
      ctx.gateway.rpc = vi.fn((method: string) =>
        method === 'config.get' ? config : method === 'session.most_recent' ? recent : Promise.resolve(null)
      )
      const onEvent = createGatewayEventHandler(ctx)
      onEvent({ type: 'runtime.ready', payload: {} } as any)

      if (phase === 'recent-pending') {
        resolveConfig({ config: { display: { tui_auto_resume_recent: true } } })
        await vi.waitFor(() => expect(ctx.gateway.rpc).toHaveBeenCalledWith('session.most_recent', {}))
      }

      selected = true
      patchUiState({ sid: 'chosen', storedSid: 'chosen', status: 'ready' })

      if (phase === 'config-error') {
        rejectConfig(new Error('config unavailable'))
      } else {
        resolveConfig({ config: { display: { tui_auto_resume_recent: phase === 'recent-pending' } } })
      }

      resolveRecent({ session_id: 'unwanted-recent' })
      await new Promise(resolve => setImmediate(resolve))
      expect(ctx.session.newSession).not.toHaveBeenCalled()
      expect(ctx.session.resumeById).not.toHaveBeenCalled()
      expect(getUiState().sid).toBe('chosen')
      expect(getUiState().status).toBe('ready')
    }
  )
  it('still starts the default session when nobody selected one', async () => {
    const ctx = buildCtx(vi.fn(async () => null))
    createGatewayEventHandler(ctx)({ type: 'runtime.ready', payload: {} } as any)
    await vi.waitFor(() => expect(ctx.session.newSession).toHaveBeenCalledOnce())
  })
})
