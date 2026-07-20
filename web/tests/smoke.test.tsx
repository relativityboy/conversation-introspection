import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import App, { HomeRedirect } from '../src/App'

describe('App shell', () => {
  it('renders the nav, main, and footer landmarks', () => {
    render(<App />)

    expect(screen.getByRole('navigation')).toBeDefined()
    expect(screen.getByRole('main')).toBeDefined()
    expect(screen.getByRole('contentinfo')).toBeDefined()
  })
})

// THE IMPORTANT (Phase 4 fixwave, half 1): the catch-all's redirect target must carry query
// params through to /search -- a pasted/typed "/?projects=x" must not silently drop the filter.
describe('HomeRedirect', () => {
  it('redirects "/" to "/search", preserving query params', () => {
    const locationRef: { current: { pathname: string; search: string } | null } = { current: null }
    function Probe() {
      const location = useLocation()
      locationRef.current = { pathname: location.pathname, search: location.search }
      return null
    }

    render(
      <MemoryRouter initialEntries={['/?projects=x']}>
        <Routes>
          <Route path="/search" element={<div>search page</div>} />
          <Route path="*" element={<HomeRedirect />} />
        </Routes>
        <Probe />
      </MemoryRouter>,
    )

    expect(locationRef.current).toEqual({ pathname: '/search', search: '?projects=x' })
  })
})
