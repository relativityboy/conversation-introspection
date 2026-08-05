// web/tests/ActionsMenu.test.tsx — actions ▾ menu: resume/download/archive relocate from three
// standalone header controls into one panel behind a single trigger (spec §3.1). Mocks
// postResume/putArchive per the client-mock convention (Sidebar.test.tsx / ArchiveButton.test.tsx);
// MemoryRouter wraps because ArchiveButton (rendered inside the panel) calls useNavigate.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ResumeResult, SessionDetail } from '../src/api/types'
import { ActionsMenu } from '../src/components/ActionsMenu'

const { postResume, putArchive } = vi.hoisted(() => ({
  postResume: vi.fn(),
  putArchive: vi.fn(),
}))

vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { ...actual, postResume, putArchive }
})

const LAUNCHED: ResumeResult = {
  restored: false,
  launched: true,
  mode: 'launched',
  command: 'claude --resume uuid-1',
  cwd: '/Users/casey/projects/myapp',
  live_path: '/tmp/x.jsonl',
  detail: null,
}

function makeSession(over: Partial<SessionDetail> = {}): SessionDetail {
  return {
    session_uuid: 'uuid-1',
    project_slug: '-Users-x-proj',
    ai_title: 'AI Title',
    custom_title: null,
    user_title: null,
    started_at: null,
    last_activity_at: null,
    message_count: 1,
    favorite: false,
    match_snippet: null,
    match_record_uuid: null,
    match_agent_hex_id: null,
    transcripts: [],
    on_disk: true,
    ...over,
  }
}

function renderMenu(over: Partial<SessionDetail> = {}) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const session = makeSession(over)
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ActionsMenu session={session} backSearch="" />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { session, ...utils }
}

beforeEach(() => {
  postResume.mockReset()
  postResume.mockResolvedValue(LAUNCHED)
  putArchive.mockReset()
  putArchive.mockResolvedValue(undefined)
})

describe('ActionsMenu', () => {
  it('is closed by default: trigger has aria-expanded="false", panel absent', () => {
    renderMenu()
    const trigger = screen.getByRole('button', { name: 'actions ▾' })
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(document.querySelector('.actions-panel')).toBeNull()
  })

  it('opens on trigger click: resume/.jsonl/archive items, aria-expanded="true"', async () => {
    renderMenu()

    await userEvent.click(screen.getByRole('button', { name: 'actions ▾' }))

    expect(screen.getByRole('button', { name: 'actions ▾' }).getAttribute('aria-expanded')).toBe(
      'true',
    )
    expect(screen.getByRole('button', { name: '⟲ resume' })).not.toBeNull()
    const link = screen.getByRole('link', { name: '↓ .jsonl' })
    expect(link.getAttribute('href')).toBe('/api/v1/sessions/uuid-1/export.jsonl')
    expect(screen.getByRole('button', { name: 'archive' })).not.toBeNull()
  })

  it('labels the resume item "restore & resume" when the live file is off disk', async () => {
    renderMenu({ on_disk: false })

    await userEvent.click(screen.getByRole('button', { name: 'actions ▾' }))

    expect(screen.getByRole('button', { name: '⟲ restore & resume' })).not.toBeNull()
  })

  it('re-clicking the trigger closes the panel', async () => {
    renderMenu()
    const trigger = screen.getByRole('button', { name: 'actions ▾' })

    await userEvent.click(trigger)
    expect(trigger.getAttribute('aria-expanded')).toBe('true')

    await userEvent.click(trigger)
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(document.querySelector('.actions-panel')).toBeNull()
  })

  it('Escape on a focused item closes the panel and returns focus to the trigger', async () => {
    renderMenu()
    const trigger = screen.getByRole('button', { name: 'actions ▾' })

    await userEvent.click(trigger)
    const archiveButton = screen.getByRole('button', { name: 'archive' })
    archiveButton.focus()
    fireEvent.keyDown(archiveButton, { key: 'Escape' })

    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(trigger)
  })

  it('mousedown on document.body closes the panel; mousedown inside the panel does not', async () => {
    renderMenu()
    const trigger = screen.getByRole('button', { name: 'actions ▾' })

    await userEvent.click(trigger)
    const panel = document.querySelector('.actions-panel') as Element
    fireEvent.mouseDown(panel)
    expect(trigger.getAttribute('aria-expanded')).toBe('true')

    fireEvent.mouseDown(document.body)
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
  })

  it('stays open while resume pends, with the resume item aria-busy', async () => {
    postResume.mockReturnValue(new Promise(() => {})) // never resolves
    renderMenu()

    await userEvent.click(screen.getByRole('button', { name: 'actions ▾' }))
    const resumeButton = screen.getByRole('button', { name: '⟲ resume' })
    await act(async () => resumeButton.click())

    expect(resumeButton.getAttribute('aria-busy')).toBe('true')
    expect(document.querySelector('.actions-panel')).not.toBeNull()
  })

  // Degradation detail stays readable (spec §3.1, honoring §17.3 of the 2026-07-13 spec): a
  // non-'launched' mode is an HTTP-200 result carrying a runnable command. It must surface as
  // BOTH the ActionButton sticky error AND a selectable .resume-detail line with the full
  // statusText sentence -- the command must not vanish in a flash or hide in a hover title.
  it('keeps the degraded resume command readable and selectable in the panel', async () => {
    postResume.mockResolvedValue({
      ...LAUNCHED,
      launched: false,
      restored: false,
      mode: 'missing_cwd',
      detail: '/gone/dir',
      command: 'claude --resume abc',
    })
    renderMenu()

    await userEvent.click(screen.getByRole('button', { name: 'actions ▾' }))
    const resumeButton = screen.getByRole('button', { name: '⟲ resume' })
    await userEvent.click(resumeButton)

    await waitFor(() => expect(resumeButton.textContent).toContain('⚠ resume failed'))
    const detail = document.querySelector('.resume-detail')
    expect(detail).not.toBeNull()
    expect(detail?.textContent).toBe(
      'original directory missing (/gone/dir) — run: claude --resume abc',
    )
    expect((detail as HTMLElement).style.userSelect).toBe('text')
  })

  it('flashes "restored & resumed ✓" on a launched + restored result', async () => {
    postResume.mockResolvedValue({ ...LAUNCHED, mode: 'launched', restored: true })
    renderMenu({ on_disk: false })

    await userEvent.click(screen.getByRole('button', { name: 'actions ▾' }))
    const resumeButton = screen.getByRole('button', { name: '⟲ restore & resume' })
    await userEvent.click(resumeButton)

    await waitFor(() => expect(resumeButton.textContent).toContain('restored & resumed ✓'))
  })
})
