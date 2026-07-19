import { QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import './App.css'
import { makeQueryClient } from './api/hooks'
import { Sidebar } from './components/Sidebar'

// Phase 3 Task 4: router + query client wired in. Sidebar (title filter, favorites, session
// list) lives in the nav region; the main pane keeps a placeholder route until the search/
// conversation-reader tasks land. Tabs, reader, and status-bar content still land in later tasks.
function App() {
  const [queryClient] = useState(() => makeQueryClient())

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <div className="app">
          <nav aria-label="Conversation archive">
            <Sidebar />
          </nav>
          <main>
            <Routes>
              <Route path="*" element={<p>Main — search and conversation reader go here.</p>} />
            </Routes>
          </main>
          <footer>
            <p>Waterline — import status goes here.</p>
          </footer>
        </div>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

export default App
