import { randomUUID } from 'node:crypto'
import { mkdtempSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { expect, it } from 'vitest'

import type { SubmissionDestination } from '../app/submissionDestination.js'
import type { QueueItem } from '../hooks/useQueue.js'
import { loadPendingInputs, migratePendingInputs, removePendingInput, savePendingInput } from '../lib/pendingInputs.js'

it('retains unsent text in this terminal without leaving plaintext files in its profile', () => {
  const home = mkdtempSync(join(tmpdir(), 'hermes-draft-'))

  const destination: SubmissionDestination = {
    sid: randomUUID(),
    storedSid: null,
    profile: 'default',
    profileHome: home
  }

  const item: QueueItem = {
    text: `private-${randomUUID()}`,
    display: 'unsent Ω',
    submissionId: randomUUID(),
    destination
  }

  try {
    savePendingInput(item)
    expect(loadPendingInputs(destination)).toEqual([item])
    expect(readdirSync(home, { recursive: true })).toEqual([])
    removePendingInput(item)
    expect(loadPendingInputs(destination)).toEqual([])
  } finally {
    removePendingInput(item)
    rmSync(home, { recursive: true, force: true })
  }
})

it('keeps profile drafts isolated and moves them only on an explicit resume mapping', () => {
  const destination: SubmissionDestination = {
    sid: randomUUID(),
    storedSid: randomUUID(),
    profile: 'a',
    profileHome: '/synthetic/a'
  }

  const item: QueueItem = { text: 'kept', display: 'kept', submissionId: randomUUID(), destination }

  try {
    savePendingInput(item)
    expect(loadPendingInputs({ ...destination, profile: 'b', profileHome: '/synthetic/b' })).toEqual([])
    const successor = { ...destination, sid: randomUUID(), storedSid: randomUUID() }
    expect(loadPendingInputs(successor)).toEqual([])
    migratePendingInputs(destination, successor.sid!, successor.storedSid!)
    expect(loadPendingInputs(successor)[0]).toMatchObject({ text: 'kept', destination: successor })
  } finally {
    removePendingInput(item)
  }
})
