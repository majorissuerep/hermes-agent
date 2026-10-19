#!/usr/bin/env node
/**
 * Generate the icon assets on demand for the pipeline that needs them.
 *
 * Default website icons are committed; CI regenerates and verifies that set.
 *
 * The renderer runs on the Hermes runtime interpreter (HERMES_PYTHON, else
 * `python` on PATH) with the declared `icons` extra selected.
 */
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { parseArgs } from 'node:util'
import { fileURLToPath, pathToFileURL } from 'node:url'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

export function generateIcons(args = [], { root = repoRoot, run = spawnSync, env = process.env } = {}) {
  const { values } = parseArgs({ args, options: {
    source: { type: 'string' }, out: { type: 'string' }, check: { type: 'boolean' },
  } })
  const source = path.resolve(values.source ?? root)
  const out = path.resolve(values.out ?? source)
  const childEnv = { ...env }
  // Parent payload paths must not shadow the runtime interpreter's own packages.
  delete childEnv.PYTHONPATH
  delete childEnv.PYTHONHOME
  const result = run(env.HERMES_PYTHON || 'python', [
    '-I', path.join(root, 'scripts', 'generate_icons.py'), '--source', source, '--out', out,
    ...(values.check ? ['--check'] : [])
  ], { cwd: source, stdio: 'inherit', windowsHide: true, env: childEnv })
  if (result.error) {
    console.error('[generate-icons] failed to launch icon generator:', result.error.message)
    console.error('[generate-icons] a Hermes runtime Python (HERMES_PYTHON or PATH) is required')
    return 1
  }
  return result.status ?? 1
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  process.exitCode = generateIcons(process.argv.slice(2))
}
