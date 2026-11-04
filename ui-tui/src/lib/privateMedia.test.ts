import { describe, expect, it } from 'vitest'

import { privateImagePreviewCommand, quotedPrivateMediaPath } from './privateMedia.js'

describe('private image preview hints', () => {
  it('quotes paths for the existing shlex slash dispatcher', () => {
    expect(privateImagePreviewCommand("/home/owner/artifacts/browser/a'b.png")).toBe(
      `/artifacts view '/home/owner/artifacts/browser/a'"'"'b.png'`
    )
    expect(quotedPrivateMediaPath('MEDIA:"/home/owner/artifacts/browser/a b.png"')).toBe(
      '/home/owner/artifacts/browser/a b.png'
    )
    expect(quotedPrivateMediaPath('MEDIA:/home/owner/artifacts/browser/a b.png')).toBe(
      '/home/owner/artifacts/browser/a b.png'
    )
  })

  it('keeps legacy encrypted images recoverable and ordinary references unchanged', () => {
    expect(privateImagePreviewCommand('/home/owner/cache/screenshots/a.png')).toContain('/artifacts view')
    expect(privateImagePreviewCommand('/home/owner/browser_screenshots/a.png')).toContain('/artifacts view')
    expect(privateImagePreviewCommand('/tmp/a.png')).toBeNull()
    expect(privateImagePreviewCommand('https://example.test/artifacts/browser/a.png')).toBeNull()
  })
})
