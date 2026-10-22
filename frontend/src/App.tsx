import { TooltipProvider } from '@/components/ui/tooltip'
import { Navbar } from '@/components/Navbar'
import { HeroSection } from '@/components/HeroSection'
import { HowItWorks } from '@/components/HowItWorks'
import { CodeTransformation } from '@/components/CodeTransformation'
import { Footer } from '@/components/Footer'

function App() {
  return (
    <TooltipProvider delayDuration={200}>
      <div className="flex min-h-screen flex-col">
        <Navbar />
        <main className="flex-1">
          <HeroSection />
          <HowItWorks />
          <CodeTransformation />
        </main>
        <Footer />
      </div>
    </TooltipProvider>
  )
}

export default App
