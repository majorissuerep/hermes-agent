import { Box, Text, useInput, useStdout } from '@hermes/ink'
import { fuzzyScore } from '@hermes/shared/fuzzy'
import type { DeckListResult, DeckPeekResult, DeckSendResult, DeckSession } from '@hermes/shared/gateway-events'
import { useStore } from '@nanostores/react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { $deck, DECK_HOME } from '../app/deckStore.js'
import type { GatewayClient } from '../gatewayClient.js'
import { asRpcResult, rpcErrorMessage } from '../lib/rpc.js'
import type { Theme } from '../theme.js'

import { windowOffset } from './overlayControls.js'
import { clampOverlayWidth, listRowStyle } from './overlayPrimitives.js'
import { TextInput } from './textInput.js'

const VISIBLE = 12
const MIN_WIDTH = 72
const MAX_WIDTH = 140
const POLL_MS = 2000
const PREVIEW_DEBOUNCE_MS = 150
const PREVIEW_MESSAGES = 4

type DeckMode = 'confirm-close' | 'filter' | 'list' | 'send'

/** Owners that ARE the session host; anything else (a classic CLI, a gateway) is worth naming. */
const HOST_SURFACES = new Set(['desktop', 'tui'])

/** Needs-you first: a waiting session is blocked on the user, a running one is spending. */
export const DECK_STATE_ORDER = ['waiting', 'running', 'idle', 'detached', 'dormant'] as const

export const deckGlyph = (state: string) =>
  ({ detached: '◌', dormant: '·', idle: '○', running: '●', waiting: '◐' })[state] ?? '?'

export const deckStateColor = (t: Theme, state: string) =>
  ({
    detached: t.color.label,
    dormant: t.color.muted,
    idle: t.color.accent,
    running: t.color.ok,
    waiting: t.color.warn
  })[state] ?? t.color.muted

/** Grouped by urgency, then by address — stable across polls so rows don't dance while states churn. */
export const sortDeck = (rows: readonly DeckSession[]) => {
  const rank = (state: string) => {
    const i = DECK_STATE_ORDER.indexOf(state as (typeof DECK_STATE_ORDER)[number])

    return i < 0 ? DECK_STATE_ORDER.length : i
  }

  const urgent = (row: DeckSession) => (row.state === 'waiting' || row.state === 'running' ? rank(row.state) : 2)

  return [...rows].sort((a, b) => urgent(a) - urgent(b) || a.profile.localeCompare(b.profile) || a.handle - b.handle)
}

export const filterDeck = (rows: readonly DeckSession[], query: string) => {
  const q = query.trim()

  if (!q) {
    return [...rows]
  }

  return rows
    .map(row => ({ row, score: fuzzyScore(`${row.ref} ${row.title} ${row.cwd} ${row.model}`, q) }))
    .filter(item => item.score !== null)
    .sort((a, b) => (b.score?.score ?? 0) - (a.score?.score ?? 0))
    .map(item => item.row)
}

export const deckSummary = (rows: readonly DeckSession[]) => {
  const counts = DECK_STATE_ORDER.map(state => [state, rows.filter(r => r.state === state).length] as const).filter(
    ([, n]) => n > 0
  )

  return [`${rows.length} open`, ...counts.map(([state, n]) => `${deckGlyph(state)} ${n} ${state}`)].join('  ·  ')
}

export const deckAge = (ts: number, now = Date.now() / 1000) => {
  const delta = Math.max(0, Math.floor(now - ts))

  for (const [unit, size] of [
    ['d', 86400],
    ['h', 3600],
    ['m', 60]
  ] as const) {
    if (delta >= size) {
      return `${Math.floor(delta / size)}${unit}`
    }
  }

  return 'now'
}

const shortPath = (path: string) => path.replace(/^\/home\/[^/]+|^\/Users\/[^/]+/, '~')

/** Absolute target so a `#N` never resolves against the wrong profile host-side. */
const targetOf = (row: DeckSession) => `${row.profile}#${row.handle}`

interface HintProps {
  items: readonly [string, string][]
  t: Theme
}

function Hints({ items, t }: HintProps) {
  return (
    <Text wrap="truncate-end">
      {items.map(([key, label], i) => (
        <Text key={key}>
          {i > 0 && <Text color={t.color.muted}> · </Text>}
          <Text color={t.color.accent}>{key}</Text>
          <Text color={t.color.muted}> {label}</Text>
        </Text>
      ))}
    </Text>
  )
}

export function SessionDeck({ currentSessionId, gw, maxWidth, onCancel, onNew, onOpen, onQuit, t }: SessionDeckProps) {
  const [rows, setRows] = useState<DeckSession[]>([])
  const [sel, setSel] = useState(0)
  const [mode, setMode] = useState<DeckMode>('list')
  const [query, setQuery] = useState('')
  const [draft, setDraft] = useState('')
  const [notice, setNotice] = useState('')
  const [err, setErr] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [preview, setPreview] = useState<DeckPeekResult | null>(null)
  const selectedRefRef = useRef<null | string>(null)
  const { generation } = useStore($deck)
  const { stdout } = useStdout()
  const width = clampOverlayWidth(Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, (stdout?.columns ?? 80) - 6)), maxWidth)

  const visible = useMemo(() => filterDeck(sortDeck(rows), query), [rows, query])
  const selected = visible[Math.min(sel, Math.max(0, visible.length - 1))]
  const hasSession = Boolean(currentSessionId)

  const load = useCallback(async () => {
    try {
      const result = asRpcResult<DeckListResult>(await gw.request('deck.list', {}))

      if (!result) {
        return setErr('invalid response: deck.list')
      }

      setErr('')
      setRows(result.sessions)
      setLoaded(true)
    } catch (e) {
      setErr(rpcErrorMessage(e))
    }
  }, [gw])

  // Poll plus push: `deck.changed` bumps the generation; the poll catches state-only drift.
  useEffect(() => {
    void load()
    const timer = setInterval(() => void load(), POLL_MS)

    return () => clearInterval(timer)
  }, [generation, load])

  // Keep the cursor on the same SESSION across refreshes/filtering, not the same row index. Runs only when the
  // list changes: cursor moves update selectedRefRef through `selected`.
  useEffect(() => {
    setSel(current => {
      const ref = selectedRefRef.current

      const anchor = ref
        ? visible.findIndex(r => r.ref === ref)
        : currentSessionId
          ? visible.findIndex(r => r.live_session_id === currentSessionId)
          : -1

      return anchor >= 0 ? anchor : Math.max(0, Math.min(current, visible.length - 1))
    })
  }, [currentSessionId, visible])

  useEffect(() => {
    selectedRefRef.current = selected?.ref ?? null
  }, [selected])

  const previewKey = selected ? `${selected.ref}:${selected.message_count}` : ''

  useEffect(() => {
    if (!selected) {
      return setPreview(null)
    }

    const target = targetOf(selected)

    const timer = setTimeout(() => {
      gw.request('deck.peek', { limit: PREVIEW_MESSAGES, target })
        .then(raw => setPreview(asRpcResult<DeckPeekResult>(raw)))
        .catch(() => setPreview(null))
    }, PREVIEW_DEBOUNCE_MS)

    return () => clearTimeout(timer)
    // previewKey (ref + message count) is the cache key: refetch only when the conversation moved.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gw, previewKey])

  const act = useCallback(
    async (method: string, params: Record<string, unknown>, done: (result: any) => string) => {
      try {
        setNotice(done(asRpcResult(await gw.request(method, params))))
        void load()
      } catch (e) {
        setNotice(`✗ ${rpcErrorMessage(e)}`)
      }
    },
    [gw, load]
  )

  const send = useCallback(
    (text: string) => {
      const message = text.trim()
      setMode('list')
      setDraft('')

      if (!message || !selected) {
        return
      }

      const ref = selected.ref
      void act('deck.send', { message, target: targetOf(selected) }, (r: DeckSendResult | null) =>
        r ? `✓ queued for ${ref}${r.woke ? ' (woke it)' : ''} — runs as its next turn` : '✗ send failed'
      )
    },
    [act, selected]
  )

  useInput((ch, key) => {
    if (mode === 'send' || mode === 'filter') {
      if (key.escape) {
        setMode('list')

        if (mode === 'filter') {
          setQuery('')
        }
      }

      return
    }

    if (mode === 'confirm-close') {
      setMode('list')

      if (ch === 'x' && selected) {
        const ref = selected.ref
        void act('deck.close', { target: targetOf(selected) }, () => `■ closed ${ref}`)
      }

      return
    }

    setNotice('')

    if (key.escape || ch === 'q') {
      if (hasSession) {
        return onCancel()
      }

      return ch === 'q' ? onQuit() : undefined
    }

    if ((key.upArrow || ch === 'k') && sel > 0) {
      return setSel(s => s - 1)
    }

    if ((key.downArrow || ch === 'j') && sel < visible.length - 1) {
      return setSel(s => s + 1)
    }

    if (key.return && selected) {
      return selected.live_session_id && selected.live_session_id === currentSessionId ? onCancel() : onOpen(selected)
    }

    if (ch === 'n') {
      return onNew()
    }

    if (ch === 'r') {
      return void load()
    }

    if (ch === '/') {
      return setMode('filter')
    }

    if (!selected) {
      return
    }

    if (ch === 's') {
      return setMode('send')
    }

    if (ch === 'x') {
      return setMode('confirm-close')
    }

    if (ch === 'i') {
      const ref = selected.ref
      void act('deck.interrupt', { target: targetOf(selected) }, r =>
        r?.interrupted ? `✓ interrupted ${ref}` : `○ ${ref} was not running`
      )
    }
  })

  const offset = windowOffset(visible.length, sel, VISIBLE)
  const shown = visible.slice(offset, offset + VISIBLE)
  const waiting = rows.filter(r => r.state === 'waiting')

  return (
    <Box flexDirection="column" width={width}>
      <Box flexDirection="row" justifyContent="space-between">
        <Text bold color={t.color.accent}>
          ◆ Session deck
        </Text>
        <Text color={t.color.muted}>{loaded ? deckSummary(rows) : 'loading…'}</Text>
      </Box>

      {waiting.length > 0 && (
        <Text color={t.color.warn} wrap="truncate-end">
          ⚡ needs you: {waiting.map(r => `${r.ref}${r.title ? ` ${r.title}` : ''}`).join('  ·  ')}
        </Text>
      )}

      {err && <Text color={t.color.error}>✗ {err}</Text>}

      {mode === 'filter' && (
        <Box>
          <Text color={t.color.accent}>/ </Text>
          <TextInput columns={width - 4} onChange={setQuery} onSubmit={() => setMode('list')} value={query} />
        </Box>
      )}

      <Box flexDirection="column" marginTop={1}>
        {loaded && !visible.length && (
          <Text color={t.color.muted}>{query ? 'no session matches' : 'no open sessions — press n to start one'}</Text>
        )}
        {offset > 0 && <Text color={t.color.muted}> ↑ {offset} more</Text>}
        {shown.map((row, i) => {
          const index = offset + i
          const active = index === sel
          const style = listRowStyle(t, active)
          const here = Boolean(row.live_session_id) && row.live_session_id === currentSessionId
          const rowColor = style.color

          return (
            <Box backgroundColor={style.backgroundColor} flexDirection="row" key={row.ref} width="100%">
              <Text color={rowColor ?? t.color.muted}>{active ? '▸' : ' '}</Text>
              <Box flexShrink={0} width={3}>
                <Text color={deckStateColor(t, row.state)}> {deckGlyph(row.state)}</Text>
              </Box>
              <Box flexShrink={0} width={11}>
                <Text bold color={rowColor ?? (here ? t.color.accent : t.color.text)} wrap="truncate-end">
                  {row.ref}
                </Text>
              </Box>
              <Box flexGrow={1} flexShrink={1} minWidth={0}>
                <Text bold={active} color={rowColor ?? (row.title ? t.color.text : t.color.muted)} wrap="truncate-end">
                  {row.title || 'untitled'}
                  {here && <Text color={rowColor ?? t.color.accent}> ◂ here</Text>}
                </Text>
              </Box>
              <Box flexShrink={0} width={10}>
                <Text color={rowColor ?? deckStateColor(t, row.state)}>{row.state}</Text>
              </Box>
              <Box flexShrink={0} justifyContent="flex-end" width={5}>
                <Text color={rowColor ?? t.color.muted}>{deckAge(row.last_active)}</Text>
              </Box>
              <Box flexShrink={0} marginLeft={2} width={18}>
                <Text color={rowColor ?? t.color.muted} wrap="truncate-start">
                  {shortPath(row.cwd)}
                </Text>
              </Box>
            </Box>
          )
        })}
        {offset + VISIBLE < visible.length && (
          <Text color={t.color.muted}> ↓ {visible.length - offset - VISIBLE} more</Text>
        )}
      </Box>

      {selected && (
        <Box
          borderBottom={false}
          borderColor={t.color.border}
          borderLeft={false}
          borderRight={false}
          borderStyle="single"
          flexDirection="column"
          marginTop={1}
        >
          <Text color={t.color.muted} wrap="truncate-end">
            {selected.ref} · {selected.model || 'model?'} · {selected.message_count} msgs
            {selected.owner_surface && !HOST_SURFACES.has(selected.owner_surface)
              ? ` · held by ${selected.owner_surface}`
              : ''}
          </Text>
          {(preview?.messages ?? []).map((m, i) => (
            <Text color={m.role === 'user' ? t.color.text : t.color.muted} key={i} wrap="truncate-end">
              <Text color={m.role === 'user' ? t.color.accent : t.color.label}>
                {m.role === 'user' ? 'you    ' : 'hermes '}›{' '}
              </Text>
              {m.text.replace(/\s+/g, ' ')}
            </Text>
          ))}
          {preview && !preview.messages.length && <Text color={t.color.muted}>no messages yet</Text>}
        </Box>
      )}

      {mode === 'send' && selected && (
        <Box marginTop={1}>
          <Text color={t.color.accent}>→ {selected.ref} › </Text>
          <TextInput columns={Math.max(20, width - 14)} onChange={setDraft} onSubmit={send} value={draft} />
        </Box>
      )}

      {mode === 'confirm-close' && selected && (
        <Text color={t.color.warn}>close {selected.ref} for good? press x again · any other key cancels</Text>
      )}

      {notice && <Text color={notice.startsWith('✗') ? t.color.error : t.color.ok}>{notice}</Text>}

      <Box marginTop={1}>
        {mode === 'send' ? (
          <Hints
            items={[
              ['⏎', 'send as its next turn'],
              ['esc', 'cancel']
            ]}
            t={t}
          />
        ) : mode === 'filter' ? (
          <Hints
            items={[
              ['⏎', 'keep filter'],
              ['esc', 'clear']
            ]}
            t={t}
          />
        ) : (
          <Hints
            items={[
              ['↑↓', 'move'],
              ['⏎', 'open'],
              ['n', 'new'],
              ['s', 'send'],
              ['i', 'stop'],
              ['x', 'close'],
              ['/', 'filter'],
              hasSession ? ['esc', 'back'] : ['q', DECK_HOME ? 'quit (sessions stay open)' : 'quit']
            ]}
            t={t}
          />
        )}
      </Box>
    </Box>
  )
}

interface SessionDeckProps {
  currentSessionId: null | string
  gw: GatewayClient
  maxWidth?: number
  onCancel: () => void
  onNew: () => void
  onOpen: (row: DeckSession) => void
  onQuit: () => void
  t: Theme
}
