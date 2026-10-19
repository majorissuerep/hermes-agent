import { usageFromTick } from '../domain/usage.js'
import type { AnyGatewayEvent } from '../gatewayTypes.js'

import { patchUiState } from './uiStore.js'

/** The caller filters other owners before forwarding these live metadata events. */
export const applySessionMetadataEvent = (ev: AnyGatewayEvent, sid: string | null) => {
  if (ev.type === 'session.title') {
    if (sid && ev.session_id === sid && typeof ev.payload?.title === 'string') {
      patchUiState({ sessionTitle: ev.payload.title.trim() })
    }

    return
  }

  // Usage ticks keep the context window current while a response is streaming.
  const usage = usageFromTick(ev.payload)

  if (usage) {
    patchUiState(state => ({ ...state, usage: { ...state.usage, ...usage } }))
  }
}
