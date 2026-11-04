import { describe, expect, it } from 'vitest'

import { privateImagePreviewCommand, privateMediaKind, quotedPrivateMediaPath } from './privateMedia.js'

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

  it('keeps MCP image preview and audio/document references truthful', () => {
    expect(privateImagePreviewCommand('/home/owner/artifacts/media/images/img.png')).toContain('/artifacts view')
    expect(privateMediaKind('/home/owner/artifacts/media/audio/audio.ogg')).toBe('audio')
    expect(privateMediaKind('/home/owner/artifacts/media/documents/doc.pdf')).toBe('documents')
    expect(quotedPrivateMediaPath('MEDIA:/home/owner/artifacts/media/audio/audio.ogg')).toBe(
      '/home/owner/artifacts/media/audio/audio.ogg'
    )
    expect(privateMediaKind('https://example.test/artifacts/media/audio/a.ogg')).toBeNull()
    expect(privateMediaKind('/home/owner/artifacts/media/config.yaml')).toBeNull()
  })
})
