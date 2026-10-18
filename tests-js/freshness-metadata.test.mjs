import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { expect, test } from 'vitest'

// The freshness contract for OS file-manager metadata (#122632, #122803): writing
// .DS_Store / AppleDouble sidecars / .localized / Thumbs.db / Desktop.ini into any
// hashed tree must never flip a recorded product from current to stale. Exercises
// the real buildInputs/recordProduct/productCurrent path against a fixture tree
// instead of a full compiler run, so the regression shows up in seconds.
const fixture = (name) => {
  const dir = mkdtempSync(path.join(tmpdir(), name))
  mkdirSync(path.join(dir, 'ui-tui/src'), { recursive: true })
  writeFileSync(path.join(dir, 'package.json'), '{"private":true}')
  writeFileSync(path.join(dir, 'package-lock.json'), '{}')
  writeFileSync(path.join(dir, 'ui-tui/src/main.ts'), 'export const answer: string = "prepared terminal";')
  return dir
}

test('OS file-manager metadata landing in hashed trees never invalidates a recorded product', async () => {
  const { buildInputs, recordProduct, productCurrent } = await import('../scripts/build/freshness.mjs')
  const source = fixture('freshness-metadata-')
  const out = mkdtempSync(path.join(tmpdir(), 'freshness-out-'))
  try {
    mkdirSync(path.join(out, 'dist'), { recursive: true })
    const inputs = buildInputs(source, 'tui')
    recordProduct({ source, product: 'tui', out, inputs })
    expect(productCurrent({ source, product: 'tui', out })).toBe(true)
    // A real source change must still invalidate — the guard stays fail-closed.
    writeFileSync(path.join(source, 'ui-tui/src/main.ts'), 'export const answer: string = "changed";')
    expect(productCurrent({ source, product: 'tui', out })).toBe(false)
    writeFileSync(path.join(source, 'ui-tui/src/main.ts'), 'export const answer: string = "prepared terminal";')
    expect(productCurrent({ source, product: 'tui', out })).toBe(true)
    // macOS Finder noise (#122632) and Windows file-manager noise (#122803) in
    // every hashed tree: source inputs, the output, prepared directories.
    for (const [name, content] of [
      ['.DS_Store', 'finder metadata'],
      ['ui-tui/src/._main.ts', 'apple double sidecar'],
      ['ui-tui/.localized', ''],
      ['ui-tui/Thumbs.db', 'thumb cache'],
      ['ui-tui/Desktop.ini', 'folder view settings'],
      ['._.DS_Store', 'double finder metadata'],
    ]) {
      writeFileSync(path.join(source, name), content)
      expect(productCurrent({ source, product: 'tui', out }), name).toBe(true)
    }
    // Same noise inside the output tree and a prepared tree must not flip it either.
    writeFileSync(path.join(out, 'dist/.DS_Store'), 'finder metadata')
    expect(productCurrent({ source, product: 'tui', out })).toBe(true)
    const prepared = mkdtempSync(path.join(tmpdir(), 'freshness-prepared-'))
    writeFileSync(path.join(prepared, 'icon.ico'), 'icon')
    writeFileSync(path.join(prepared, 'Thumbs.db'), 'thumb cache')
    const withPrepared = buildInputs(source, 'tui', { icons: prepared })
    recordProduct({ source, product: 'tui', out, inputs: withPrepared })
    writeFileSync(path.join(prepared, '.DS_Store'), 'finder metadata')
    expect(productCurrent({ source, product: 'tui', out, prepared: { icons: prepared } })).toBe(true)
    // ...while a genuine prepared-input change still invalidates.
    writeFileSync(path.join(prepared, 'icon.ico'), 'changed icon')
    expect(productCurrent({ source, product: 'tui', out, prepared: { icons: prepared } })).toBe(false)
  } finally {
    rmSync(source, { recursive: true, force: true })
    rmSync(out, { recursive: true, force: true })
  }
})
