/** A private image reference is persistent ciphertext, so never offer file:// as a viewer. */
export function privateImagePreviewCommand(path: string): string | null {
  const normalized = path.replaceAll('\\', '/')

  if (
    !/^(?:\/|[a-z]:\/)/i.test(normalized) ||
    !/(?:\/artifacts\/browser\/|\/cache\/screenshots\/|\/browser_screenshots\/)/.test(normalized)
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

  return path && privateImagePreviewCommand(path) ? path : null
}
