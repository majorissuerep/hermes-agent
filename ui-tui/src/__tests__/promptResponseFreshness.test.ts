import { expect, it } from 'vitest'

import { capturePromptResponseGuard, patchOverlayState } from '../app/overlayStore.js'
import { patchUiState, resetUiState } from '../app/uiStore.js'

it('allows only the original prompt at its owning session to publish a response', () => {
  resetUiState()
  const info = { model: 'test', tools: {}, skills: {} }
  patchUiState({ sid: 'original', info })

  for (const key of ['approval', 'clarify', 'sudo', 'secret'] as const) {
    const prompt = { requestId: 'same-id' } as any
    patchOverlayState({ [key]: prompt })
    const fresh = capturePromptResponseGuard(key, prompt)
    expect(fresh()).toBe(true)
    patchUiState({ sid: 'other' })
    expect(fresh()).toBe(false)
    patchUiState({ sid: 'original', info })
    expect(fresh()).toBe(true)
    patchOverlayState({ [key]: { ...prompt } })
    expect(fresh()).toBe(false)
    patchOverlayState({ [key]: null })
  }

  resetUiState()
})
