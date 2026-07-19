import { QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import './App.css'
import { makeQueryClient } from './api/hooks'
import { Sidebar } from './components/Sidebar'
import { StatusBar } from './components/StatusBar'
import { TabBar } from './components/TabBar'
import { SearchPage } from './routes/SearchPage'
import { SessionPage } from './routes/SessionPage'
import { SubagentPage } from './routes/SubagentPage'

// Phase 3 Task 8: the reading room is complete. Global search (/search), the conversation reader
// (/s/:uuid, /s/:uuid/m/:msgUuid), and the subagent drill-in (/s/:uuid/a/:agentHex[/m/:msgUuid])
// route below a route-derived TabBar; the waterline StatusBar occupies the footer on every route.
// The catch-all placeholder remains for the home surface (no session selected).
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
            <TabBar />
            <div className="main-view">
              <Routes>
                <Route path="/search" element={<SearchPage />} />
                <Route path="/s/:uuid" element={<SessionPage />} />
                <Route path="/s/:uuid/m/:msgUuid" element={<SessionPage />} />
                <Route path="/s/:uuid/a/:agentHex" element={<SubagentPage />} />
                <Route path="/s/:uuid/a/:agentHex/m/:msgUuid" element={<SubagentPage />} />
                <Route path="*" element={<Navigate to="/search" replace />} />
              </Routes>
            </div>
          </main>
          <footer>
            <StatusBar />
          </footer>
        </div>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

export default App
