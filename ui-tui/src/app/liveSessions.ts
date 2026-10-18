import type { SessionActiveListResponse } from '../gatewayTypes.js'
import type { LocalAgentClient } from '../localAgentClient.js'
/** The live-session roster the status bar's title and count read: on the shared owner its own
 * `session.list` (live scope); the sidecar's `session.active_list` never holds those sessions. */
export const requestLiveSessions = (gw: Pick<LocalAgentClient, 'request'>, currentSid: null | string) =>
  gw.request<SessionActiveListResponse>('session.active_list', { current_session_id: currentSid })
