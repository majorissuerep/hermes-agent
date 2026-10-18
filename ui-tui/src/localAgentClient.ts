import { type ChildProcessWithoutNullStreams, spawn } from 'node:child_process'
import { EventEmitter } from 'node:events'
import { closeSync } from 'node:fs'
import { resolve } from 'node:path'
import { createInterface } from 'node:readline'

import { JsonRpcRequestChannel, type ServerRequest } from '@hermes/shared/json-rpc-channel'

import type { AnyGatewayEvent } from './gatewayTypes.js'
import { CircularBuffer } from './lib/circularBuffer.js'

const STARTUP_TIMEOUT_MS = 60_000
const REQUEST_TIMEOUT_MS = 120_000

/** One terminal owns one Python runtime. No listener, discovery or remote attachment. */
export class LocalAgentClient extends EventEmitter {
  private child: ChildProcessWithoutNullStreams | null = null
  private disposed = false
  private subscribed = false
  private ready = false
  private starting: Promise<void> | null = null
  private resolveReady: (() => void) | null = null
  private rejectReady: ((error: Error) => void) | null = null
  private readyTimer: ReturnType<typeof setTimeout> | null = null
  private logs = new CircularBuffer<string>(200)
  private events = new CircularBuffer<AnyGatewayEvent>(2000)
  private requests: ServerRequest[] = []
  private pendingExit: number | null | undefined
  private readonly channel = new JsonRpcRequestChannel({
    onEvent: event => this.publish(event as AnyGatewayEvent),
    requestTimeoutMs: REQUEST_TIMEOUT_MS,
    unrefTimers: true
  })

  constructor() {
    super()
    this.setMaxListeners(0)
    this.channel.onRequest(request => {
      if (this.subscribed) {
        this.emit('request', request)
      } else {
        this.requests.push(request)
      }
    })
  }

  private publish(event: AnyGatewayEvent) {
    if (event.type === 'runtime.ready') {
      this.ready = true

      if (this.readyTimer) {
        clearTimeout(this.readyTimer)
      }

      this.readyTimer = null
      this.resolveReady?.()
    }

    if (this.subscribed) {
      this.emit('event', event)
    } else {
      this.events.push(event)
    }
  }

  start() {
    if (this.disposed || this.child) {
      return
    }

    const root = process.env.HERMES_PYTHON_SRC_ROOT ?? resolve(import.meta.dirname, '../../')
    const python = process.env.HERMES_PYTHON?.trim() || (process.platform === 'win32' ? 'python' : 'python3')
    this.starting = new Promise<void>((resolveReady, rejectReady) => {
      this.resolveReady = resolveReady
      this.rejectReady = rejectReady
    })
    // Readiness also has event consumers; startup failure must not become an unhandled rejection.
    void this.starting.catch(() => {})
    const env: NodeJS.ProcessEnv = { ...process.env, PYTHONPATH: root }
    const keyFd = /^\d+$/.test(env.HERMES_VAULT_KEY_FD ?? '') ? Number(env.HERMES_VAULT_KEY_FD) : null
    const stdio: Array<'pipe' | number> = ['pipe', 'pipe', 'pipe']

    if (keyFd !== null) {
      if (keyFd < 3) {
        throw new Error('Invalid inherited vault descriptor')
      }

      stdio.push(keyFd)
      env.HERMES_VAULT_KEY_FD = '3'
    }

    let child: ChildProcessWithoutNullStreams

    try {
      child = spawn(python, ['-m', 'tui_runtime'], {
        cwd: root,
        env,
        stdio,
        windowsHide: true
      }) as ChildProcessWithoutNullStreams
    } finally {
      if (keyFd !== null) {
        closeSync(keyFd)
        delete process.env.HERMES_VAULT_KEY_FD
      }
    }

    this.child = child
    this.recordLog(`[runtime] ${python} -m tui_runtime`)
    this.channel.attach({
      send: text => {
        if (child.stdin.destroyed) {
          throw new Error('Local runtime input closed')
        }

        child.stdin.write(text + '\n')
      }
    })
    const output = createInterface({ input: child.stdout })
    output.on('line', line => this.channel.handleFrame(line))
    const diagnostics = createInterface({ input: child.stderr })
    diagnostics.on('line', line => {
      this.recordLog(line)
      this.publish({ type: 'runtime.stderr', payload: { line } })
    })
    child.once('error', error => {
      this.recordLog(error.message)
      this.failed(error)
    })
    child.once('close', code => {
      output.close()
      diagnostics.close()

      if (this.child !== child) {
        return
      }

      this.child = null
      this.disposed = true
      this.failed(new Error(`Local runtime exited (${code ?? 'signal'})`))

      if (this.subscribed) {
        this.emit('exit', code)
      } else {
        this.pendingExit = code
      }
    })
    this.readyTimer = setTimeout(() => {
      if (this.ready) {
        return
      }

      this.failed(new Error('Local runtime startup timed out'))
      this.publish({
        type: 'runtime.start_timeout',
        payload: {
          python,
          cwd: root,
          stderr_tail: this.getLogTail()
        }
      })
      child.kill('SIGTERM')
    }, STARTUP_TIMEOUT_MS)
  }

  private failed(error: Error) {
    if (this.readyTimer) {
      clearTimeout(this.readyTimer)
    }

    this.readyTimer = null
    this.rejectReady?.(error)
    this.channel.detach(error)
  }

  request<T = unknown>(method: string, params: Record<string, unknown> = {}, timeoutMs?: number): Promise<T> {
    if (this.disposed) {
      return Promise.reject(new Error('Local runtime closed'))
    }

    this.start()

    return this.starting!.then(() => this.channel.request<T>(method, params, timeoutMs))
  }

  recordLog(line: string) {
    this.logs.push(line.length > 4096 ? line.slice(0, 4096) + '…' : line)
  }

  getLogTail(limit = 20): string {
    return this.logs.tail(Math.max(1, limit)).join('\n')
  }

  publishLocalEvent(event: AnyGatewayEvent) {
    this.publish(event)
  }

  drain() {
    queueMicrotask(() => {
      if (this.disposed) {
        return
      }

      this.subscribed = true

      for (const event of this.events.drain()) {
        this.emit('event', event)
      }

      for (const request of this.requests.splice(0)) {
        this.emit('request', request)
      }

      if (this.pendingExit !== undefined) {
        const code = this.pendingExit
        this.pendingExit = undefined
        this.emit('exit', code)
      }
    })
  }

  kill(reason = 'requested') {
    this.disposed = true
    this.recordLog(`[runtime] close: ${reason}`)
    this.failed(new Error('Local runtime closed'))
    this.child?.stdin.end()
    this.child?.kill('SIGTERM')
  }
}
