import { expect, it } from 'vitest'

import { terminalSessionTitles, titleFromActiveSessions } from '../app/sessionTitles.js'

it('keeps a recognizable identity before naming and uses the stored name across status changes', () => {
  expect(terminalSessionTitles('✓', '', 'session-abc123', 'model', '/projects/hermes').tab).toBe('✓ hermes · abc123')
  expect(terminalSessionTitles('✓', '', '', '', '').tab).toBe('✓ Hermes')

  for (const marker of ['✓', '⏳', '⚠']) {
    const result = terminalSessionTitles(marker, 'Fix the parser', 'session-abc123', 'model', '/projects/hermes')
    expect(result.tab).toBe(`${marker} Fix the parser`)
    expect(result.window).toContain('Fix the parser · model · ')
  }
})

it('does not clear a title on partial polls or apply a response from the previous owner', () => {
  expect(titleFromActiveSessions([], 'owner', 'owner', 'New name')).toBe('New name')
  expect(titleFromActiveSessions([{ id: 'owner', title: '' }], 'owner', 'owner', 'New name')).toBe('New name')
  expect(titleFromActiveSessions([{ id: 'old', title: 'Old name' }], 'old', 'owner', 'New name')).toBe('New name')
  expect(titleFromActiveSessions([{ id: 'owner', title: 'Resumed name' }], 'owner', 'owner', '')).toBe('Resumed name')
  expect(
    titleFromActiveSessions([{ id: 'owner', title: 'Old name' }], 'owner', 'owner', 'Renamed while polling', 'Old name')
  ).toBe('Renamed while polling')
})
