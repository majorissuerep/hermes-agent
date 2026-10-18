import { PassThrough } from 'node:stream'

import { renderSync } from '@hermes/ink'
import React from 'react'
import stripAnsi from 'strip-ansi'
import { expect, it, vi } from 'vitest'

import { ModelPicker } from '../components/modelPicker.js'
import type { LocalAgentClient } from '../localAgentClient.js'
import { DEFAULT_THEME } from '../theme.js'

it('filters providers and models with pasted names as well as individual keys', async () => {
  const stdout = Object.assign(new PassThrough(), { columns: 100, rows: 30, isTTY: false })
  const stdin = Object.assign(new PassThrough(), { isTTY: true, setRawMode: () => {}, ref: () => {}, unref: () => {} })
  const onSelect = vi.fn()
  let output = ''

  stdout.on('data', chunk => { output += stripAnsi(chunk.toString()) })
  const request = vi.fn(async () => ({ model: 'fixture-alpha', providers: [
    { slug: 'fixture', name: 'Fixture Endpoint', authenticated: true,
      models: ['fixture-alpha', 'fixture-beta'], capabilities: { 'fixture-beta': { reasoning: false } } }
  ] }))

  const view = renderSync(<ModelPicker gw={{ request } as unknown as LocalAgentClient}
    onCancel={vi.fn()} onSelect={onSelect} sessionId="owner" t={DEFAULT_THEME} />, {
    stdout: stdout as unknown as NodeJS.WriteStream,
    stdin: stdin as unknown as NodeJS.ReadStream,
    stderr: new PassThrough() as unknown as NodeJS.WriteStream, patchConsole: false
  })

  try {
    await vi.waitFor(() => expect(output).toContain('Select provider'))
    stdin.write('\x1b[200~Fixture\x1b[201~')
    await vi.waitFor(() => expect(output).toContain('filter: Fixture'))
    stdin.write('\r')
    await vi.waitFor(() => expect(output).toContain('Select model'))
    output = ''
    stdin.write('f')
    await vi.waitFor(() => expect(output).toContain('filter: f'))
    stdin.write('\x1b[200~ixture-beta\x1b[201~')
    await vi.waitFor(() => expect(output).toContain('filter: fixture-beta'))
    stdin.write('\r')
    await vi.waitFor(() => expect(onSelect).toHaveBeenCalledWith('fixture-beta --provider fixture --tui-session'))
  } finally {
    view.unmount()
  }
})
