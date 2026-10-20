import type { InputDetectDropResponse, PromptSubmitResponse } from '../gatewayTypes.js'
import type { QueueItem } from '../hooks/useQueue.js'
import { t } from '../i18n/runtime.js'
import { pendingInputOwner, savePendingInput } from '../lib/pendingInputs.js'
import type { LocalAgentClient } from '../localAgentClient.js'
import type { Msg } from '../types.js'

import { captureDestination, isCurrentDestination, type SubmissionDestination } from './submissionDestination.js'
import { turnController } from './turnController.js'
import { patchUiState } from './uiStore.js'
const SESSION_BUSY_RE = /session busy|waiting for model response/i
export const isSessionBusyError = (e: unknown) => e instanceof Error && SESSION_BUSY_RE.test(e.message)
export interface SubmitPromptDeps {
  appendMessage: (msg: Msg) => void
  enqueue: (text: string, display?: string, destination?: SubmissionDestination) => void
  expand: (text: string) => string
  gw: LocalAgentClient
  setLastUserMsg: (value: string) => void
  sys: (text: string) => void
}

// Optimistically flip the session to busy the INSTANT a prompt is accepted for
// submission — synchronously, before we await anything.
//
// This is the fix for the queue-mode race (display.busy_input_mode: queue):
// the submit path first fires an async `input.detect_drop` RPC and only marked
// the session busy inside that RPC's `.then`. A second Enter pressed inside
// that round-trip window read `busy === false` in dispatchSubmission and raced
// a second `prompt.submit` onto the backend instead of landing in the local
// queue. That produced the reported symptom: the second message "waited for
// the first to respond, then went to the queue", and the client lost track of
// it (the backend accepts a mid-turn submit as {status:"queued"} — a success,
// not an error — so the local drain effect that watches the client-side queue
// never fires, leaving the UI stuck on "analyzing…" until Ctrl+C).
//
// Marking busy at the choke point closes the gap for every caller: the mainline
// submit, queue-edit picks, and the drain effect all funnel through here.
export function markSubmitting(): void {
  patchUiState({ busy: true, status: 'running…' })
}

// A voice-mode transcript about to be submitted: the matching prompt.submit carries
// `voice_turn` so the gateway runs it on `auxiliary.voice_chat`. Matched by text, so a
// transcript the user edited or that went to the queue still lands as typed text.
let pendingVoiceTranscript: null | string = null

export function markNextSubmitVoice(text: string): void {
  pendingVoiceTranscript = text
}

function takeVoiceTranscript(): null | string {
  const transcript = pendingVoiceTranscript
  pendingVoiceTranscript = null

  return transcript
}

interface SubmitPromptOpts {
  attachments?: Array<{
    path: string
    mime: string
  }>
  skipDetectDrop?: boolean
  destination?: SubmissionDestination
  queueItem?: QueueItem
  behindTurn?: boolean
}

function beginOwnedTurn(
  deps: SubmitPromptDeps,
  text: string,
  displayOverride: string | undefined,
  displayText: string,
  show: boolean
): void {
  turnController.clearStatusTimer()
  deps.setLastUserMsg(text)

  if (show) {
    deps.appendMessage({ role: 'user', text: displayOverride || displayText })
  }

  patchUiState({ busy: true, status: 'running…' })
  turnController.bufRef = ''
  turnController.interrupted = false
}

function prepareQueueItem(item: QueueItem, submitText: string, opts: SubmitPromptOpts): void {
  item.preparedText ??= submitText

  item.attachments ??= opts.attachments?.map(attachment => ({ ...attachment }))

  savePendingInput(item)
}

function submitParams(
  sid: string,
  item: QueueItem | undefined,
  submitText: string,
  attachments: SubmitPromptOpts['attachments'],
  voice: boolean
) {
  return {
    session_id: sid,
    text: item?.preparedText ?? submitText,
    ...((item?.attachments ?? attachments)?.length ? { attachments: item?.attachments ?? attachments } : {}),
    ...(item ? { queued: item.queued !== false } : {}),
    // A busy correction (steer/redirect) is not a turn: it never claims the voice route.
    ...(voice && { voice_turn: true })
  }
}

// Submit a ready prompt (already resolved to be neither a slash command nor a
// shell escape, with a live session). Pulled out of useSubmission so the
// synchronous-busy invariant above is unit-testable without React test infra.
//
// `displayOverride` is what the transcript shows when it differs from what the
// agent receives — a `/skill` invocation expands into the whole skill body, and
// that scaffolding is model-facing only.
export function submitPrompt(
  text: string,
  deps: SubmitPromptDeps,
  showUserMessage = true,
  displayOverride?: string,
  opts: SubmitPromptOpts = {}
): void {
  // The voice marker belongs to the input submitted right after it, whatever happens to that input
  // (busy correction, refusal): consumed here, so a later identical typed prompt never inherits it.
  const voiceTranscript = takeVoiceTranscript()
  const destination = opts.destination ?? captureDestination()
  const owner = pendingInputOwner(opts.queueItem?.ownerDestination ?? destination)
  const { sid } = owner
  const focused = () => isCurrentDestination(owner)
  // A busy-time admission joins the authority FIFO behind the running turn:
  // it must not touch the in-flight stream buffer, status or transcript. Its
  // user bubble is painted when the fanout reports the row started.
  const ownsTurn = () => focused() && !opts.behindTurn

  if (!sid) {
    return deps.sys('session not ready yet')
  }

  // An identityless write cannot be deduplicated, even after the owner upgrades.
  if (opts.queueItem?.deliveryAttempted) {
    opts.queueItem.settle?.(false)

    if (focused()) {
      deps.sys(t('canonical.submit.deliveryNotResent'))
      patchUiState({ status: t('canonical.submit.statusDeliveryUnconfirmed') })
    }

    return
  }

  // Close the async-busy gap up front, before the detect_drop round-trip.
  if (ownsTurn()) {
    markSubmitting()
  }

  const startSubmit = (displayText: string, submitText: string, show = true) => {
    if (ownsTurn()) {
      beginOwnedTurn(deps, text, displayOverride, displayText, show)
    } else if (focused() && show) {
      deps.appendMessage({ role: 'user', text: displayOverride || displayText })
    }

    const item = opts.queueItem

    if (item) {
      prepareQueueItem(item, submitText, opts)
      item.deliveryAttempted = true
    }

    deps.gw
      .request<PromptSubmitResponse>(
        'prompt.submit',
        submitParams(sid, item, submitText, opts.attachments, !opts.behindTurn && voiceTranscript === submitText.trim())
      )
      .then(r => {
        const accepted = Boolean(
          r?.voice_stopped || ['streaming', 'queued', 'steered', 'redirected'].includes(r?.status ?? '')
        )

        item?.settle?.(accepted)

        // The gateway consumed a typed voice stop phrase server-side (voice
        // chat ended, no turn started) — release the busy latch; the
        // voice.transcript {stop_phrase} event handles the mode flags + notice.
        if (r?.voice_stopped && focused()) {
          patchUiState({ busy: false, status: 'ready' })
        }
      })
      .catch((e: Error) => {
        if (item) {
          item.settle?.(false)

          if (focused()) {
            deps.sys(t('canonical.submit.inputRetained', e.message))
            patchUiState({ status: t('canonical.submit.statusAdmissionUnconfirmed') })
          }

          return
        }

        // Defensive: prompt.submit no longer rejects a mid-turn send with
        // "session busy" (the gateway queues it and returns success), but keep
        // the re-queue path as a safety net for any future/legacy gateway that
        // still errors, so a message is never silently dropped.
        if (isSessionBusyError(e)) {
          deps.enqueue(submitText, displayOverride, destination)

          if (!focused()) {
            return
          }

          patchUiState({ busy: true, status: 'queued for next turn' })

          return deps.sys(`queued: "${submitText.slice(0, 50)}${submitText.length > 50 ? '…' : ''}"`)
        }

        if (!focused()) {
          return
        }

        deps.sys(`error: ${e.message}`)
        patchUiState({ busy: false, status: 'ready' })
      })
  }

  // Always ask the backend whether this looks like a file drop. The backend's
  // _detect_file_drop handles paths with spaces, quotes, Windows drive letters,
  // and escaped characters correctly. Literal submissions (startup -q queries)
  // skip it: launcher-provided text must reach the agent untouched.
  //
  // No notice is emitted for a match: an image dropped into the composer already
  // shows as an `[[ Image N ]]` token, and a matched non-image path is rewritten
  // in place. Announcing it a second time above the status bar was the old
  // out-of-band attachment UI.
  if (opts.queueItem?.preparedText !== undefined) {
    return startSubmit(text, opts.queueItem.preparedText, showUserMessage)
  }

  if (opts.skipDetectDrop) {
    return startSubmit(text, deps.expand(text), showUserMessage)
  }

  deps.gw
    .request<InputDetectDropResponse>('input.detect_drop', { session_id: sid, text })
    .then(r => {
      if (!r?.matched) {
        return startSubmit(text, deps.expand(text), showUserMessage)
      }

      startSubmit(r.text || text, deps.expand(r.text || text), showUserMessage)
    })
    .catch(() => startSubmit(text, deps.expand(text), showUserMessage))
}
