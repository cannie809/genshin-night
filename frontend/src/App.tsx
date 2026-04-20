import { GameBoard } from './components/GameBoard'
import { NicknameGate } from './components/NicknameGate'
import { RoomLobby } from './components/RoomLobby'
import { RoomView } from './components/RoomView'
import { useGameStore } from './store/gameStore'

function App() {
  const displayName = useGameStore((s) => s.displayName)
  const currentRoomId = useGameStore((s) => s.currentRoomId)
  const gameState = useGameStore((s) => s.gameState)

  // Routing priority:
  //  1. No nickname → NicknameGate (first-run only)
  //  2. In a room AND game is live → GameBoard
  //  3. In a room → RoomView (waiting / finished / pre-game states)
  //  4. Else → RoomLobby
  let content: React.ReactNode
  if (!displayName) {
    content = <NicknameGate />
  } else if (currentRoomId && gameState) {
    content = <GameBoard />
  } else if (currentRoomId) {
    content = <RoomView roomId={currentRoomId} />
  } else {
    content = <RoomLobby />
  }

  return (
    <div className="bg-background text-foreground relative min-h-screen overflow-hidden">
      {/* Atmospheric background gradient */}
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(ellipse_at_top,_#1a0a2e_0%,_transparent_50%)]" />
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(ellipse_at_bottom_right,_#1a0000_0%,_transparent_40%)]" />
      <div className="pointer-events-none fixed inset-0 bg-[radial-gradient(ellipse_at_top_left,_#1a1400_0%,_transparent_30%)]" />
      <div className="relative z-10">{content}</div>
    </div>
  )
}

export default App
