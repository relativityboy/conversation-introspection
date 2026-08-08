import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BlockOut, MessageOut, TranscriptInfo } from '../src/api/types'
import { RawRecordInspector } from '../src/components/reader/RawRecordInspector'
import { TranscriptsProvider } from '../src/components/reader/transcripts-context'
import type { ViewMode } from '../src/lib/viewMode'

// Mock the api client module (useRawRecord imports fetchRawRecord directly) — same convention as
// the other reader tests. Each test drives the raw payload per uuid so navigation is observable.
const { fetchRawRecord } = vi.hoisted(() => ({ fetchRawRecord: vi.fn() }))

vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { ...actual, fetchRawRecord }
})

function textBlock(): BlockOut {
  return {
    block_index: 0,
    block_kind: 'text',
    text_content: 'hi',
    tool_name: null,
    tool_use_id: null,
    is_error: null,
  }
}

function msg(
  uuid: string,
  type = 'user',
  blocks: BlockOut[] = [textBlock()],
  authorship: Partial<
    Pick<MessageOut, 'authorship_kind' | 'authorship_basis' | 'authorship_detail'>
  > = {},
): MessageOut {
  return {
    record_uuid: uuid,
    parent_uuid: null,
    type,
    model: null,
    timestamp: null,
    blocks,
    authorship_kind: null,
    authorship_basis: null,
    authorship_detail: null,
    ...authorship,
  }
}

// A distinctive, uuid-keyed payload so each record's content is greppable in the pretty view.
function payloadFor(uuid: string): string {
  return JSON.stringify({ marker: `content-${uuid}` })
}

function Harness({
  items,
  initialUuid,
  parentView = 'all',
  transcripts = [],
}: {
  items: MessageOut[]
  initialUuid: string
  parentView?: ViewMode
  /** Threaded through a real TranscriptsProvider (default: empty, matching the un-provided
   * default context) so navigation tests can exercise the resolved-dispatch tool_use id set the
   * same way MessageTurn does. */
  transcripts?: TranscriptInfo[]
}) {
  const [open, setOpen] = useState(false)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return (
    <QueryClientProvider client={queryClient}>
      <TranscriptsProvider value={{ sessionUuid: 'sess', transcripts }}>
        <button type="button" onClick={() => setOpen(true)}>
          opener
        </button>
        {open && (
          <RawRecordInspector
            items={items}
            initialUuid={initialUuid}
            parentView={parentView}
            onClose={() => setOpen(false)}
          />
        )}
      </TranscriptsProvider>
    </QueryClientProvider>
  )
}

async function open(): Promise<HTMLElement> {
  const opener = screen.getByRole('button', { name: 'opener' })
  await userEvent.click(opener)
  await screen.findByRole('dialog')
  return opener
}

function dialog(): HTMLElement {
  return screen.getByRole('dialog')
}

function preText(): string {
  // Supports both raw bytes (<pre> with direct text) and pretty mode (<pre><code> nested).
  const pre = dialog().querySelector('pre')
  return pre?.textContent ?? ''
}

beforeEach(() => {
  fetchRawRecord.mockReset()
  fetchRawRecord.mockImplementation((uuid: string) => Promise.resolve(payloadFor(uuid)))
})

// --- open / close / focus -----------------------------------------------------------------

describe('open and close', () => {
  it('opens a dialog showing pretty-printed JSON for the initial record', async () => {
    fetchRawRecord.mockResolvedValue('{"a":1,"b":2}')
    render(<Harness items={[msg('uuid-0')]} initialUuid="uuid-0" />)
    await open()

    // Pretty view reformats with 2-space indent + a space after the colon.
    await waitFor(() => expect(preText()).toContain('"a": 1'))
    expect(preText()).toContain('"b": 2')
  })

  it('closes on Escape and returns focus to the opening button', async () => {
    render(<Harness items={[msg('uuid-0')]} initialUuid="uuid-0" />)
    const opener = await open()
    await screen.findByText(/content-uuid-0/)

    fireEvent.keyDown(dialog(), { key: 'Escape' })

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    await waitFor(() => expect(document.activeElement).toBe(opener))
  })

  it('closes when the close button is clicked', async () => {
    render(<Harness items={[msg('uuid-0')]} initialUuid="uuid-0" />)
    await open()
    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

// --- authorship classification line (spec §6) ----------------------------------------------

describe('authorship classification line', () => {
  function authorshipLine(): string | null {
    return dialog().querySelector('.raw-record-authorship')?.textContent ?? null
  }

  it('renders kind (basis — detail) above the raw JSON when classified with a detail', async () => {
    const classified = msg('uuid-0', 'user', [textBlock()], {
      authorship_kind: 'skill_injection',
      authorship_basis: 'verified',
      authorship_detail: 'superpowers:brainstorming',
    })
    render(<Harness items={[classified]} initialUuid="uuid-0" />)
    await open()
    expect(authorshipLine()).toBe(
      'authorship: skill_injection (verified — superpowers:brainstorming)',
    )
  })

  it('omits the em-dash detail suffix when authorship_detail is null', async () => {
    const classified = msg('uuid-0', 'user', [textBlock()], {
      authorship_kind: 'tool_result',
      authorship_basis: 'verified',
      authorship_detail: null,
    })
    render(<Harness items={[classified]} initialUuid="uuid-0" />)
    await open()
    expect(authorshipLine()).toBe('authorship: tool_result (verified)')
  })

  it('renders the pre-backfill fallback when authorship_kind is null', async () => {
    render(<Harness items={[msg('uuid-0')]} initialUuid="uuid-0" />)
    await open()
    expect(authorshipLine()).toBe('authorship: not yet classified (reparse pending)')
  })
})

// --- pretty / raw toggle + malformed fallback ---------------------------------------------

describe('pretty / raw toggle', () => {
  it('shows the untouched bytes under the "raw bytes" toggle, reformatted JSON without it', async () => {
    fetchRawRecord.mockResolvedValue('{"a":1}')
    render(<Harness items={[msg('uuid-0')]} initialUuid="uuid-0" />)
    await open()

    // Pretty: reformatted (has the "a": 1 spacing).
    await waitFor(() => expect(preText()).toContain('"a": 1'))

    await userEvent.click(screen.getByRole('button', { name: 'raw bytes' }))
    // Raw: byte-identical to the stored line, no reformatting.
    await waitFor(() => expect(preText()).toBe('{"a":1}'))
  })

  it('falls back to the raw text with a notice when the line is not valid JSON', async () => {
    fetchRawRecord.mockResolvedValue('{"broken": ')
    render(<Harness items={[msg('uuid-0')]} initialUuid="uuid-0" />)
    await open()

    await screen.findByText(/Not valid JSON/)
    expect(preText()).toBe('{"broken": ')
  })
})

// --- navigation: traversal order, view filtering, edges -------------------------------------

describe('navigation', () => {
  // A zero-block attachment is hidden under a filtered view; user rows flank it.
  const items = [msg('uuid-0'), msg('uuid-1', 'attachment', []), msg('uuid-2')]

  it('prev/next skips filtered-out rows when the modal filter is chat', async () => {
    render(<Harness items={items} initialUuid="uuid-0" parentView="chat" />)
    await open()
    await screen.findByText(/content-uuid-0/)

    await userEvent.click(screen.getByRole('button', { name: 'Next record' }))

    // Skipped the hidden attachment (uuid-1), landed on uuid-2.
    await waitFor(() => expect(fetchRawRecord).toHaveBeenLastCalledWith('uuid-2'))
    await screen.findByText(/content-uuid-2/)
  })

  it('navigates via Left/Right arrow hotkeys', async () => {
    render(<Harness items={items} initialUuid="uuid-0" />)
    await open()
    await screen.findByText(/content-uuid-0/)

    // Filter off / view=all (default here): Right visits every loaded row, including the
    // attachment.
    fireEvent.keyDown(dialog(), { key: 'ArrowRight' })
    await waitFor(() => expect(fetchRawRecord).toHaveBeenLastCalledWith('uuid-1'))
    fireEvent.keyDown(dialog(), { key: 'ArrowLeft' })
    await waitFor(() => expect(fetchRawRecord).toHaveBeenLastCalledWith('uuid-0'))
  })

  it('the in-modal filter follows the parent view by default and can be switched to all', async () => {
    render(<Harness items={items} initialUuid="uuid-0" parentView="chat" />)
    await open()
    const chatSegment = screen.getByRole('button', { name: 'chat' })
    expect(chatSegment.getAttribute('aria-pressed')).toBe('true') // follows parent

    // With the filter on chat, uuid-0's next is uuid-2 (attachment hidden).
    await userEvent.click(screen.getByRole('button', { name: 'Next record' }))
    await waitFor(() => expect(fetchRawRecord).toHaveBeenLastCalledWith('uuid-2'))

    // Switch to all: now the previously-hidden attachment (uuid-1) is reachable via prev.
    await userEvent.click(screen.getByRole('button', { name: 'all' }))
    expect(chatSegment.getAttribute('aria-pressed')).toBe('false')
    await userEvent.click(screen.getByRole('button', { name: 'Previous record' }))
    await waitFor(() => expect(fetchRawRecord).toHaveBeenLastCalledWith('uuid-1'))
  })

  // Final review C1: prev/next must land on a RESOLVED dispatch row (its only block is the
  // tool_use, production shape) instead of skipping it, and the SAME predicate MessageTurn uses
  // to decide row visibility must drive this navigation — a real TranscriptsProvider is what
  // supplies the resolved-dispatch tool_use id set here, exactly as it does in the reader.
  it('prev/next lands on a resolved dispatch row (no prose) instead of skipping it', async () => {
    function toolUseBlock(id: string): BlockOut {
      return {
        block_index: 0,
        block_kind: 'tool_use',
        text_content: null,
        tool_name: 'Task',
        tool_use_id: id,
        is_error: null,
      }
    }
    const dispatchOnly = msg('uuid-dispatch', 'assistant', [toolUseBlock('tu-1')], {
      authorship_kind: 'claude',
    })
    const dispatchItems = [msg('uuid-0'), dispatchOnly, msg('uuid-2')]
    const dispatchTranscript: TranscriptInfo = {
      id: 2,
      kind: 'subagent',
      agent_hex_id: 'a1b2c3',
      agent_type: 'Explore',
      agent_description: null,
      parent_tool_use_id: 'tu-1',
    }

    render(
      <Harness
        items={dispatchItems}
        initialUuid="uuid-0"
        parentView="chat"
        transcripts={[dispatchTranscript]}
      />,
    )
    await open()
    await screen.findByText(/content-uuid-0/)

    await userEvent.click(screen.getByRole('button', { name: 'Next record' }))

    // Landed on the resolved dispatch row, not skipped straight to uuid-2.
    await waitFor(() => expect(fetchRawRecord).toHaveBeenLastCalledWith('uuid-dispatch'))
    await screen.findByText(/content-uuid-dispatch/)
  })

  it('disables the arrow at each window edge (no edge-fetch from the modal)', async () => {
    const twoVisible = [msg('uuid-0'), msg('uuid-2')]
    const { unmount } = render(<Harness items={twoVisible} initialUuid="uuid-0" />)
    await open()
    // First row: no previous.
    expect((screen.getByRole('button', { name: 'Previous record' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Next record' }) as HTMLButtonElement).disabled).toBe(false)
    unmount()

    render(<Harness items={twoVisible} initialUuid="uuid-2" />)
    await open()
    // Last row: no next.
    expect((screen.getByRole('button', { name: 'Next record' }) as HTMLButtonElement).disabled).toBe(true)
  })
})

// --- hotkey scoping + esc isolation -------------------------------------------------------

// --- JSON colorization via the prose pipeline -----------------------------------------------

describe('JSON colorization', () => {
  it('pretty mode colorizes JSON tokens via the prose pipeline; raw bytes stays plain', async () => {
    fetchRawRecord.mockResolvedValue('{"a": 1, "b": "two", "c": true}')
    render(<Harness items={[msg('uuid-0')]} initialUuid="uuid-0" />)
    await open()

    // Pretty (default): token spans exist inside the prose-scoped wrapper.
    const wrapper = document.querySelector('.raw-record-json')
    expect(wrapper).not.toBeNull()
    // Check for at least one of the hljs token classes that rehype-highlight produces.
    await waitFor(() => {
      const hasTokens = wrapper?.querySelector('.hljs-attr, .hljs-string, .hljs-number')
      expect(hasTokens).not.toBeNull()
    })

    // Toggle raw bytes: plain pre, no token spans.
    await userEvent.click(screen.getByRole('button', { name: 'raw bytes' }))
    await waitFor(() => {
      expect(document.querySelector('.raw-record-bytes')).not.toBeNull()
      expect(document.querySelector('.raw-record-json')).toBeNull()
    })
  })
})

// --- hotkey scoping + esc isolation -------------------------------------------------------

describe('hotkey scoping', () => {
  it('ignores arrow keys while the modal is closed', async () => {
    render(<Harness items={[msg('uuid-0'), msg('uuid-1')]} initialUuid="uuid-0" />)
    // Modal closed: a global arrow press must do nothing (no dialog, no fetch).
    fireEvent.keyDown(document.body, { key: 'ArrowRight' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(fetchRawRecord).not.toHaveBeenCalled()
  })

  it('stops firing hotkeys once closed', async () => {
    render(<Harness items={[msg('uuid-0'), msg('uuid-1')]} initialUuid="uuid-0" />)
    await open()
    await screen.findByText(/content-uuid-0/)

    fireEvent.keyDown(dialog(), { key: 'ArrowRight' })
    await waitFor(() => expect(fetchRawRecord).toHaveBeenLastCalledWith('uuid-1'))

    fireEvent.keyDown(dialog(), { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())

    fetchRawRecord.mockClear()
    // No handler survives the unmount, so a further arrow press fetches nothing.
    fireEvent.keyDown(document.body, { key: 'ArrowRight' })
    expect(fetchRawRecord).not.toHaveBeenCalled()
  })

  it("Esc does not reach a document-level keydown listener (TitleEditor's esc machine)", async () => {
    const docListener = vi.fn()
    document.addEventListener('keydown', docListener)
    try {
      render(<Harness items={[msg('uuid-0')]} initialUuid="uuid-0" />)
      await open()
      await screen.findByText(/content-uuid-0/)

      fireEvent.keyDown(dialog(), { key: 'Escape' })

      await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
      expect(docListener).not.toHaveBeenCalled()
    } finally {
      document.removeEventListener('keydown', docListener)
    }
  })
})
