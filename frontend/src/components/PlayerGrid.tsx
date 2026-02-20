import { Users } from 'lucide-react'
import { useGameStore } from '../store/gameStore'
import { PlayerCard } from './PlayerCard'
import { Separator } from '@/components/ui/separator'

interface PlayerGridProps {
  onPlayerSelect?: (playerId: string) => void
  selectedPlayerId?: string
}

export function PlayerGrid({ onPlayerSelect, selectedPlayerId }: PlayerGridProps) {
  const { gameState } = useGameStore()

  if (!gameState) return null

  const aliveCount = gameState.players.filter((p) => p.alive).length

  return (
    <div>
      <div className="mb-4 flex items-center gap-2">
        <Users className="text-muted-foreground size-5" />
        <h2 className="font-display text-lg font-semibold tracking-wide">玩家列表</h2>
        <Separator className="bg-border/50 flex-1" />
        <span className="text-muted-foreground text-xs">
          存活 {aliveCount}/{gameState.players.length}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
        {gameState.players.map((player) => (
          <PlayerCard
            key={player.id}
            player={player}
            isSelected={selectedPlayerId === player.id}
            onClick={() => onPlayerSelect?.(player.id)}
            winner={gameState.winner}
          />
        ))}
      </div>
    </div>
  )
}
