import { useEffect } from 'react'
import { Route, Routes, useLocation } from 'react-router-dom'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Navbar } from '@/components/Navbar'
import { Footer } from '@/components/Footer'
import { AuthProvider } from '@/lib/auth'
import { LandingPage } from '@/pages/LandingPage'
import { ScanPage } from '@/pages/ScanPage'
import { IssuePage } from '@/pages/IssuePage'
import { NotFoundPage } from '@/pages/NotFoundPage'


function ScrollToTop() {
  const { pathname } = useLocation()
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [pathname])
  return null
}

function App() {
  return (
    <TooltipProvider delayDuration={200}>
      <AuthProvider>
        <ScrollToTop />
        <div className="flex min-h-screen flex-col">
          <Navbar />
          <main className="flex-1">
            <Routes>
              <Route path="/" element={<LandingPage />} />
              <Route path="/scan/:runId" element={<ScanPage />} />
              <Route
                path="/scan/:runId/issues/:issueId"
                element={<IssuePage />}
              />
              <Route path="*" element={<NotFoundPage />} />
            </Routes>
          </main>
          <Footer />
        </div>
      </AuthProvider>
    </TooltipProvider>
  )
}

export default App
