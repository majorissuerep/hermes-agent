import { describe, expect, it } from 'vitest'

import { takeQueueItem } from '../hooks/useQueue.js'

const destination = { sid: 'chat-a', storedSid: 'chat-a' } as any
const attachments = [{ mime: 'image/png', path: '/w/shot.png' }]

describe('editing the text of a local queued item', () => {
  it('keeps its attachments, destination and queue destination, and starts a new submission', () => {
    const queue = [
      {
        attachments,
        destination,
        display: 'look at this',
        failed: true,
        ownerDestination: destination,
        preparedText: 'look at this [image]',
        submissionId: 'sub-1',
        text: 'look at this'
      }
    ]

    const edited = takeQueueItem(queue, 0, 'look at this closely')

    expect(edited).toEqual({
      attachments,
      destination,
      display: 'look at this closely',
      ownerDestination: destination,
      text: 'look at this closely'
    })
  })
})
