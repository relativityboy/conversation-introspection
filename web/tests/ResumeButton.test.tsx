// web/tests/ResumeButton.test.tsx — ArchiveButton.test.tsx conventions: mock the client module,
// real useMutation runs.
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ResumeResult } from '../src/api/types'
import { ResumeButton } from '../src/components/ResumeButton'

const { postResume } = vi.hoisted(() => ({ postResume: vi.fn() }))

vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { ...actual, postResume }
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

function renderButton(onDisk = true) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <ResumeButton sessionUuid="uuid-1" onDisk={onDisk} />
    </QueryClientProvider>,
  )
  return { invalidateSpy, ...utils }
}

beforeEach(() => {
  postResume.mockReset()
  postResume.mockResolvedValue(LAUNCHED)
})

describe('ResumeButton', () => {
  it('labels honestly from on_disk', () => {
    renderButton(true)
    expect(screen.getByRole('button', { name: '⟲ resume' })).not.toBeNull()
  })

  it('labels restore & resume when the live file is gone', () => {
    renderButton(false)
    expect(screen.getByRole('button', { name: '⟲ restore & resume' })).not.toBeNull()
  })

  it('posts and reports a launch, invalidating the session cache', async () => {
    const { invalidateSpy } = renderButton()
    await userEvent.click(screen.getByRole('button', { name: '⟲ resume' }))
    expect(postResume).toHaveBeenCalledWith('uuid-1')
    await waitFor(() => expect(screen.getByText('launched')).not.toBeNull())
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['sessions'] }),
    )
  })

  it('composes restored + launched', async () => {
    postResume.mockResolvedValue({ ...LAUNCHED, restored: true })
    renderButton(false)
    await userEvent.click(screen.getByRole('button', { name: '⟲ restore & resume' }))
    await waitFor(() =>
      expect(screen.getByText('restored from archive · launched')).not.toBeNull(),
    )
  })

  it('keeps the command readable when the launch degrades', async () => {
    postResume.mockResolvedValue({
      ...LAUNCHED,
      launched: false,
      mode: 'missing_cwd',
      detail: '/gone/dir',
    })
    renderButton()
    await userEvent.click(screen.getByRole('button', { name: '⟲ resume' }))
    await waitFor(() =>
      expect(
        screen.getByText('original directory missing (/gone/dir) — run: claude --resume uuid-1'),
      ).not.toBeNull(),
    )
  })

  it('reports failure without pretending', async () => {
    postResume.mockRejectedValue(new Error('boom'))
    renderButton()
    await userEvent.click(screen.getByRole('button', { name: '⟲ resume' }))
    await waitFor(() => expect(screen.getByText('resume failed')).not.toBeNull())
  })
})
