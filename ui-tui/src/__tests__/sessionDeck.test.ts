import type { DeckSession } from '@hermes/shared/gateway-events'
import { describe, expect, it } from 'vitest'

import { deckAge, deckSummary, filterDeck, sortDeck } from '../components/sessionDeck.js'

const row = (over: Partial<DeckSession>): DeckSession => ({
  cwd: '/work',
  estimated_cost_usd: null,
  group_name: '',
  handle: 1,
  last_active: 0,
  live_session_id: '',
  message_count: 0,
  model: 'm',
  opened_at: 0,
  owner_pid: null,
  owner_surface: '',
  parent_handle: null,
  profile: 'default',
  ref: '#1',
  session_id: 's',
  state: 'idle',
  surface: 'tui',
  tip_session_id: 's',
  title: '',
  ...over
})

describe('session deck ordering', () => {
  it('puts sessions that need the user first, then keeps address order stable', () => {
    const rows = [
      row({ handle: 1, ref: '#1', state: 'dormant' }),
      row({ handle: 2, ref: '#2', state: 'running' }),
      row({ handle: 3, ref: '#3', state: 'idle' }),
      row({ handle: 4, ref: '#4', state: 'waiting' }),
      row({ handle: 5, ref: '#5', state: 'detached' })
    ]

    expect(sortDeck(rows).map(r => r.ref)).toEqual(['#4', '#2', '#1', '#3', '#5'])
  })

  it('does not reorder settled rows when their states churn', () => {
    const before = [row({ handle: 1, ref: '#1', state: 'idle' }), row({ handle: 2, ref: '#2', state: 'detached' })]
    const after = [row({ handle: 1, ref: '#1', state: 'dormant' }), row({ handle: 2, ref: '#2', state: 'idle' })]

    expect(sortDeck(after).map(r => r.ref)).toEqual(sortDeck(before).map(r => r.ref))
  })
})

describe('session deck filter', () => {
  it('matches across ref, title and cwd and keeps everything on an empty query', () => {
    const rows = [row({ ref: '#1', title: 'Refactor auth' }), row({ cwd: '/srv/etl', ref: '#2', title: 'Import' })]

    expect(filterDeck(rows, 'auth').map(r => r.ref)).toEqual(['#1'])
    expect(filterDeck(rows, 'etl').map(r => r.ref)).toEqual(['#2'])
    expect(filterDeck(rows, '  ')).toHaveLength(2)
  })
})

describe('session deck labels', () => {
  it('counts every open session and names only the states present', () => {
    const summary = deckSummary([row({ state: 'running' }), row({ state: 'running' }), row({ state: 'dormant' })])

    expect(summary.startsWith('3 open')).toBe(true)
    expect(summary).toContain('2 running')
    expect(summary).not.toContain('waiting')
  })

  it('ages coarsely', () => {
    expect(deckAge(1000, 1030)).toBe('now')
    expect(deckAge(1000, 1000 + 7200)).toBe('2h')
  })
})
