import { createHash } from 'node:crypto'
import { once } from 'node:events'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { createServer } from 'node:http'
import os from 'node:os'
import path from 'node:path'

import { expect, test } from 'vitest'

import { downloadPinnedArtifact } from './artifact'

test('a resumable download retains and reuses its hash-verified durable bytes', async (): Promise<void> => {
  const directory: string = await mkdtemp(path.join(os.tmpdir(), 'hermes-artifact-lifetime-'))
  const bytes: Buffer = Buffer.from('digest-bound download')
  let requests: number = 0
  const server = createServer((_request, response): void => {
    requests += 1
    response.end(bytes)
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  if (!address || typeof address === 'string') {
    throw new Error('Expected TCP server')
  }
  const artifact = {
    url: `http://127.0.0.1:${address.port}/stable.zip`,
    sha256: createHash('sha256').update(bytes).digest('hex'),
    size: bytes.length,
    format: 'zip' as const
  }
  try {
    const downloaded: string = await downloadPinnedArtifact(directory, artifact)
    expect(await downloadPinnedArtifact(directory, artifact)).toBe(downloaded)
    expect(await readFile(downloaded)).toEqual(bytes)
    expect(requests).toBe(1)
  } finally {
    await new Promise<void>((resolve): void => { server.close((): void => { resolve() }) })
    await rm(directory, { recursive: true, force: true })
  }
})
