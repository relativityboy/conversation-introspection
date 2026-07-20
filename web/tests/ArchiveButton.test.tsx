import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ArchiveButton } from '../src/components/ArchiveButton'

// Mock the api client module (not global fetch) -- same convention as TitleEditor.test.tsx:
// hooks.ts (useArchiveSession) imports putArchive directly, so this swaps the network layer in
// one place while the real useMutation/invalidation/navigation logic runs.
const { putArchive } = vi.hoisted(() => ({ putArchive: vi.fn() }))

vi.mock('../src/api/client', async () => {
  const actual = await vi.importActual<typeof import('../src/api/client')>('../src/api/client')
  return { ...actual, putArchive }
})

function LocationProbe() {
  const loc = useLocation()
  return <div data-testid="loc">{loc.pathname}</div>
}

function renderButton() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/s/uuid-1']}>
        <LocationProbe />
        <Routes>
          <Route path="/s/:uuid" element={<ArchiveButton sessionUuid="uuid-1" />} />
          <Route path="/" element={<div>home</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { invalidateSpy, ...utils }
}

beforeEach(() => {
  putArchive.mockReset()
  putArchive.mockResolvedValue(undefined)
})

describe('ArchiveButton', () => {
  it('archives the session then navigates to / on success', async () => {
    renderButton()
    expect(screen.getByTestId('loc').textContent).toBe('/s/uuid-1')

    await userEvent.click(screen.getByRole('button', { name: 'archive' }))

    expect(putArchive).toHaveBeenCalledWith('uuid-1')
    await waitFor(() => expect(screen.getByTestId('loc').textContent).toBe('/'))
  })

  it('invalidates the sessions and search caches on success', async () => {
    const { invalidateSpy } = renderButton()

    await userEvent.click(screen.getByRole('button', { name: 'archive' }))

    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['sessions'] })
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['search'] })
    })
  })

  it('does NOT navigate when the archive request fails', async () => {
    putArchive.mockRejectedValue(new Error('boom'))
    renderButton()

    await userEvent.click(screen.getByRole('button', { name: 'archive' }))

    await waitFor(() => expect(putArchive).toHaveBeenCalled())
    // Still on the conversation -- a failed archive must not strand the user on the home surface.
    expect(screen.getByTestId('loc').textContent).toBe('/s/uuid-1')
  })
})
