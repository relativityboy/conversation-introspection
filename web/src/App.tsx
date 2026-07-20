import { QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import './App.css'
import { makeQueryClient } from './api/hooks'
import { ProjectFilterBar } from './components/ProjectFilterBar'
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
// THE IMPORTANT (Phase 4 fixwave, half 1): the catch-all below redirects "/" to "/search", but a
// bare `<Navigate to="/search" />` drops whatever query string a pasted/typed "/?projects=…" URL
// carried. This makes the redirect param-transparent instead.
export function HomeRedirect() {
  const location = useLocation()
  return <Navigate to={{ pathname: '/search', search: location.search }} replace />
}

function App() {
  const [queryClient] = useState(() => makeQueryClient())

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <div className="app">
          <ProjectFilterBar />
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
                <Route path="*" element={<HomeRedirect />} />
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
