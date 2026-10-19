import { PassThrough } from 'stream'

import { renderSync } from '@hermes/ink'
import React, { useEffect } from 'react'
import { expect, it, vi } from 'vitest'

import { useTerminalTitle } from '../../packages/hermes-ink/src/ink/hooks/use-terminal-title.js'
import { TerminalWriteContext } from '../../packages/hermes-ink/src/ink/useTerminalNotification.js'

it('writes separate readable tab/window titles once per value, not per streaming render', async () => {
  const write = vi.fn()
  const painted = vi.fn()

  const Harness = ({ tick, title }: { tick: number; title: string }) => {
    useTerminalTitle({ tab: `✓ ${title}`, window: `✓ ${title} · model · ~/project` })
    useEffect(() => {
      painted(tick)
    }, [tick])

    return null
  }

  const tree = (tick: number, title: string) => (
    <TerminalWriteContext.Provider value={write}>
      <Harness tick={tick} title={title} />
    </TerminalWriteContext.Provider>
  )

  const stdout = Object.assign(new PassThrough(), { columns: 80, rows: 24, isTTY: false })
  const stdin = Object.assign(new PassThrough(), { isTTY: false })
  const stderr = Object.assign(new PassThrough(), { isTTY: false })

  const instance = renderSync(tree(0, 'Parser'), {
    stdout: stdout as NodeJS.WriteStream,
    stdin: stdin as NodeJS.ReadStream,
    stderr: stderr as NodeJS.WriteStream,
    patchConsole: false
  })

  try {
    await vi.waitFor(() => expect(painted).toHaveBeenCalledWith(0))
    expect(write).toHaveBeenCalledTimes(1)
    expect(write.mock.calls[0][0]).toContain('\x1b]1;✓ Parser')
    expect(write.mock.calls[0][0]).toContain('\x1b]2;✓ Parser · model · ~/project')
    instance.rerender(tree(1, 'Parser'))
    await vi.waitFor(() => expect(painted).toHaveBeenCalledWith(1))
    expect(write).toHaveBeenCalledTimes(1)
    instance.rerender(tree(2, 'Renamed'))
    await vi.waitFor(() => expect(painted).toHaveBeenCalledWith(2))
    expect(write).toHaveBeenCalledTimes(2)
    expect(write.mock.calls[1][0]).toContain('\x1b]1;✓ Renamed')
  } finally {
    instance.unmount()
    instance.cleanup()
  }
})
