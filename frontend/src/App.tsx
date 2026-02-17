import { GameBoard } from './components/GameBoard'

function App() {
  return (
    <div className="min-h-screen bg-background text-foreground relative overflow-hidden">
      {/* Atmospheric background gradient */}
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(ellipse_at_top,_#1a0a2e_0%,_transparent_50%)]" />
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(ellipse_at_bottom_right,_#1a0000_0%,_transparent_40%)]" />
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(ellipse_at_top_left,_#1a1400_0%,_transparent_30%)]" />
      <div className="relative z-10">
        <GameBoard />
      </div>
    </div>
  )
}

export default App
