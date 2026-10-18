import { realpathSync } from 'node:fs'
import { homedir } from 'node:os'
import { basename, dirname, join, resolve } from 'node:path'

import { getUiState } from './uiStore.js'

export interface SubmissionDestination {
  readonly sid: string | null
  readonly storedSid?: string | null
  readonly profile: string
  readonly profileHome: string
  /** The owner's profile identity its receipts name (`target_profile_home`). */
}

// Resolve existing ancestors too: a new profile may not have been created yet.
function canonicalHome(path: string): string {
  try {
    return realpathSync(path)
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') {
      throw error
    }

    const parent = dirname(path)

    return parent === path ? path : join(canonicalHome(parent), basename(path))
  }
}

export function captureDestination(): SubmissionDestination {
  const { sid, info } = getUiState()
  const profileHome = canonicalHome(resolve(process.env.HERMES_HOME ?? join(homedir(), '.hermes')))

  return Object.freeze({
    sid,
    storedSid: info?.stored_session_id || null,
    profile: info?.profile_name || 'default',
    profileHome
  })
}

export function isCurrentDestination(destination: SubmissionDestination): boolean {
  const current = captureDestination()

  return (
    current.sid === destination.sid &&
    current.profile === destination.profile &&
    current.profileHome === destination.profileHome
  )
}
