import { expect, it } from 'vitest'

import { createGatewayEventHandler } from '../app/createGatewayEventHandler.js'
import { getUiState, patchUiState, resetUiState } from '../app/uiStore.js'

import { buildGatewayEventContext } from './gatewayEventFixture.js'

it('applies the focused owner title immediately and keeps it through partial snapshots', () => {
  resetUiState()
  patchUiState({ sid: 'focused', storedSid: 'durable', sessionTitle: 'old name', busy: true })
  const onEvent = createGatewayEventHandler(buildGatewayEventContext([]))
  onEvent({ type: 'session.title', session_id: 'other', payload: { session_id: 'other-key', title: 'wrong' } } as any)
  expect(getUiState().sessionTitle).toBe('old name')
  onEvent({
    type: 'session.title',
    session_id: 'focused',
    payload: { session_id: 'durable', title: 'Build the parser' }
  } as any)
  expect(getUiState().sessionTitle).toBe('Build the parser')
  onEvent({ type: 'session.info', session_id: 'focused', payload: { running: true } } as any)
  expect(getUiState().sessionTitle).toBe('Build the parser')
})
