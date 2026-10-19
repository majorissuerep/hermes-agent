import { expect, it, vi } from 'vitest'

import { turnController } from '../app/turnController.js'
import { patchUiState, resetUiState } from '../app/uiStore.js'

it('stops the local display immediately but reports a refused native interrupt once', async () => {
  resetUiState()
  turnController.fullReset()
  patchUiState({ sid: 'owner', busy: true })
  const request = vi.fn(async () => { throw new Error('owner unavailable') })
  const sys = vi.fn()

  try {
    await turnController.interruptTurn({ appendMessage: vi.fn(), gw: { request }, sid: 'owner', sys })
    await Promise.resolve()
    expect(turnController.interrupted).toBe(true)
    expect(request).toHaveBeenCalledExactlyOnceWith('session.interrupt', { session_id: 'owner' })
    expect(sys).toHaveBeenCalledWith('interrupt failed: owner unavailable')
  } finally {
    turnController.fullReset()
    resetUiState()
  }
})
