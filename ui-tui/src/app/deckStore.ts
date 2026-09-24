import type { DeckRegisterResult } from '@hermes/shared/gateway-events'
import { atom } from 'nanostores'

import type { GatewayClient } from '../gatewayClient.js'
import { asRpcResult } from '../lib/rpc.js'

/**
 * Deck mode: this client is attached to the machine session host (`hermes_cli/tui_deck_launch.py`).
 * Sessions live in the host, so leaving one DETACHES it (it keeps running, open in the deck) instead
 * of closing it; only `/close` or the deck's close ends a session. The host's env and cwd are the
 * machine root's, so the client supplies its profile and working directory on create/resume.
 */
export const DECK_MODE = process.env.HERMES_TUI_DECK === '1'

/** `hermes deck`: start on the deck instead of forging a fresh session. */
export const DECK_HOME = DECK_MODE && process.env.HERMES_TUI_DECK_HOME === '1'

const DECK_PROFILE = process.env.HERMES_TUI_PROFILE || ''
const DECK_CWD = process.env.HERMES_TUI_CWD || ''

export interface DeckState {
  /** Bumped on every `deck.changed` so open deck views refetch. */
  generation: number
  openCount: number
  /** This client's current session in deck notation (`#7`, `work#3`). */
  ref: null | string
}

export const $deck = atom<DeckState>({ generation: 0, openCount: 0, ref: null })

export const patchDeck = (next: Partial<DeckState>) => $deck.set({ ...$deck.get(), ...next })

export const deckCreateParams = (): Record<string, string> =>
  DECK_MODE ? { ...(DECK_PROFILE && { profile: DECK_PROFILE }), ...(DECK_CWD && { cwd: DECK_CWD }) } : {}

export const deckResumeParams = (profile?: string): Record<string, string> => {
  const target = profile || DECK_PROFILE

  return DECK_MODE && target ? { profile: target } : {}
}

/** Mark a freshly created/resumed session open in the deck (idempotent host-side). */
export const registerDeckSession = async (gw: GatewayClient, sessionId: string) => {
  if (!DECK_MODE) {
    return null
  }

  const result = asRpcResult<DeckRegisterResult>(await gw.request('deck.register', { session_id: sessionId }))

  patchDeck({ ref: result?.ref ?? null })

  return result
}
