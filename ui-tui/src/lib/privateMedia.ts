/** A private image reference is persistent ciphertext, so never offer file:// as a viewer. */
export function privateMediaKind(path: string): string | null {
  const normalized = path.replaceAll('\\', '/')

  if (!/^(?:\/|[a-z]:\/)/i.test(normalized)) {
    return null
  }

  const canonical = normalized.match(/\/artifacts\/media\/(images|audio|documents)\/[^/]+$/)?.[1]

  if (canonical) {
    return canonical
  }

  const generated = normalized.match(/\/artifacts\/generated\/(images|videos)\/[^/]+$/)?.[1]
    ?? normalized.match(/\/cache\/generated\/(images|videos)\/[a-z0-9_.-]+_\d{8}_\d{6}_[a-f0-9]{8}\.[a-z0-9]{1,12}$/i)?.[1]

  if (generated) {
    return generated
  }

  if (/\/artifacts\/generated\/pet-store\/[a-f0-9]{32}\/(?:spritesheet\.(?:png|webp)|thumbnail\.png)$/.test(normalized)
    || /\/(?:artifacts\/generated\/pet-drafts|cache\/pet-gen)\/[a-f0-9]{12}\/(?:draft-\d+\.png|reference\.(?:png|jpg|jpeg|webp|gif))$/.test(normalized)) {
    return 'images'
  }

  const legacy = normalized.match(
    /\/(cache\/(?:images|audio|documents)|image_cache|audio_cache|document_cache)\/(img|audio|doc)_[a-f0-9]{32}\.[a-z0-9]{1,12}$/i
  )

  if (!legacy) {
    return null
  }

  const kinds: Record<string, string> = { img: 'images', audio: 'audio', doc: 'documents' }

  return kinds[legacy[2]] ?? null
}

export function privateImagePreviewCommand(path: string): string | null {
  const normalized = path.replaceAll('\\', '/')

  if (
    !/^(?:\/|[a-z]:\/)/i.test(normalized) ||
    (!/(?:\/artifacts\/(?:browser|media\/images)\/|\/cache\/screenshots\/|\/browser_screenshots\/)/.test(normalized) &&
      privateMediaKind(path) !== 'images')
  ) {
    return null
  }

  // POSIX shlex quoting is also consumed by the existing Python slash worker.
  return `/artifacts view '${path.replaceAll("'", "'\"'\"'")}'`
}

export function quotedPrivateMediaPath(line: string): string | null {
  let text = line.trim()

  if (/^[`"']MEDIA:/.test(text) && text.at(-1) === text[0]) {
    text = text.slice(1, -1)
  }

  if (!text.startsWith('MEDIA:')) {
    return null
  }

  let path = text.slice('MEDIA:'.length).trim()

  if (/^[`"']/.test(path) && path.at(-1) === path[0]) {
    path = path.slice(1, -1)
  }

  return path && (privateImagePreviewCommand(path) || privateMediaKind(path)) ? path : null
}
