import type { SubmissionDestination } from '../app/submissionDestination.js'
import type { QueueItem } from '../hooks/useQueue.js'

// Unsent drafts belong to this terminal's lifetime. Durable conversation content is written only
// by Python to SQLCipher; Node must never journal user text to plaintext files.
const pending = new Map<string, QueueItem>()
const successors = new Map<string, SubmissionDestination>()
export let pendingInputRevision = 0

export const pendingDestinationKey = (destination: SubmissionDestination) =>
  JSON.stringify([destination.profileHome, destination.profile, destination.sid])

export function pendingInputOwner(destination: SubmissionDestination): SubmissionDestination {
  let current = destination
  const seen = new Set<string>()

  while (!seen.has(pendingDestinationKey(current))) {
    const key = pendingDestinationKey(current)
    seen.add(key)
    const next = successors.get(key)

    if (!next) {
      break
    }

    current = next
  }

  return current
}

export function migratePendingInputs(previous: SubmissionDestination, sid: string, storedSid?: string): void {
  if (!previous.sid || !sid || (previous.sid === sid && previous.storedSid === storedSid)) {
    return
  }

  const successor = Object.freeze({ ...previous, sid, storedSid: storedSid ?? null })

  for (const item of loadPendingInputs(previous, true)) {
    successors.set(pendingDestinationKey(item.ownerDestination ?? item.destination!), successor)
    item.ownerDestination = successor

    if (!item.failed) {
      item.destination = successor
    }
  }

  successors.set(pendingDestinationKey(previous), successor)
  pendingInputRevision++
}

export function savePendingInput(item: QueueItem): void {
  if (item.submissionId && item.destination?.sid) {
    pending.set(item.submissionId, { ...item })
  }
}

export function removePendingInput(item: QueueItem): void {
  if (item.submissionId) {
    pending.delete(item.submissionId)
  }
}

export function loadPendingInputs(destination: SubmissionDestination, resume = false): QueueItem[] {
  return [...pending.values()]
    .filter(item => {
      const owner = pendingInputOwner(item.ownerDestination ?? item.destination!)

      return (
        owner.profileHome === destination.profileHome &&
        owner.profile === destination.profile &&
        (resume
          ? (owner.storedSid || owner.sid) === (destination.storedSid || destination.sid)
          : owner.sid === destination.sid)
      )
    })
    .sort((a, b) => (a.createdAt ?? 0) - (b.createdAt ?? 0))
}
