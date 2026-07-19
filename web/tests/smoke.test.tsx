import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from '../src/App'

describe('App shell', () => {
  it('renders the nav, main, and footer landmarks', () => {
    render(<App />)

    expect(screen.getByRole('navigation')).toBeDefined()
    expect(screen.getByRole('main')).toBeDefined()
    expect(screen.getByRole('contentinfo')).toBeDefined()
  })
})
