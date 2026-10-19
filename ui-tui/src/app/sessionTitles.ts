import { composeTabTitle, shortCwd } from '../domain/paths.js'

/** Keep the tab recognizable even before auto-naming has a conversation to summarize. */
export const terminalSessionTitles = (
  marker: string,
  title: string,
  sid: string | null,
  model: string,
  cwd: string
) => {
  const project = cwd.replace(/\/+$/, '').split('/').pop() ?? ''
  const identity = title.trim() || [project, sid ? sid.slice(-6) : ''].filter(Boolean).join(' · ') || 'Hermes'

  return {
    tab: composeTabTitle(marker, identity, '', ''),
    window: composeTabTitle(marker, identity, model, cwd ? shortCwd(cwd, 24) : '')
  }
}

/** A partial/late active-list response must not erase a newer owner title. */
export const titleFromActiveSessions = (
  sessions: { id: string; title?: string | null }[],
  requestedSid: string | null,
  currentSid: string | null,
  currentTitle: string,
  requestedTitle = currentTitle
) =>
  requestedSid === currentSid && requestedTitle === currentTitle
    ? sessions.find(session => session.id === currentSid)?.title?.trim() || currentTitle
    : currentTitle
