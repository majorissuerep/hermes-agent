import { beforeEach, expect, it } from 'vitest'

import { createGatewayEventHandler } from '../app/createGatewayEventHandler.js'
import { resetOverlayState } from '../app/overlayStore.js'
import { turnController } from '../app/turnController.js'
import { resetTurnState } from '../app/turnStore.js'
import { getUiState, patchUiState, resetUiState } from '../app/uiStore.js'
import type { Msg } from '../types.js'

import { buildGatewayEventContext } from './gatewayEventFixture.js'

beforeEach(() => {
  resetOverlayState()
  resetUiState()
  resetTurnState()
  turnController.fullReset()
})

it('shows the reply but keeps its owner busy until the authoritative idle snapshot', () => {
  const messages: Msg[] = []
  const onEvent = createGatewayEventHandler(buildGatewayEventContext(messages))
  patchUiState({ sid: 'owner', info: { model: 'test', tools: {}, skills: {} } })

  const emit = (type: string, payload: unknown, sessionId = 'owner') =>
    onEvent({ type, payload, session_id: sessionId } as any)

  emit('message.start', {})
  turnController.showNotice({ kind: 'sticky', key: 'owner-done', text: 'Owner settled' })
  emit('message.complete', { text: 'Final answer', status: 'complete', running: true })

  expect(messages.at(-1)).toMatchObject({ role: 'assistant', text: 'Final answer' })
  expect(getUiState().busy).toBe(true)
  expect(getUiState().status).not.toBe('ready')
  expect(getUiState().notice).toBeNull()

  emit('session.info', { running: false }, 'other-owner')
  emit('session.info', {})
  emit('session.info', { running: true })
  expect(getUiState().busy).toBe(true)

  emit('session.info', { running: false })
  expect(getUiState().busy).toBe(false)
  expect(getUiState().status).toBe('ready')
  expect(getUiState().notice?.text).toBe('Owner settled')
})
