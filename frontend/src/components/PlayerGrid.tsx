import { Users } from 'lucide-react';
import { useGameStore } from '../store/gameStore';
import { PlayerCard } from './PlayerCard';
import { Separator } from '@/components/ui/separator';

interface PlayerGridProps {
  onPlayerSelect?: (playerId: string) => void;
  selectedPlayerId?: string;
}

export function PlayerGrid({ onPlayerSelect, selectedPlayerId }: PlayerGridProps) {
  const { gameState } = useGameStore();

  if (!gameState) return null;

  const aliveCount = gameState.players.filter(p => p.alive).length;

  return (
    <div>
      <div className="flex items-center gap-2 mb-4">
        <Users className="size-5 text-muted-foreground" />
        <h2 className="text-lg font-display font-semibold tracking-wide">玩家列表</h2>
        <Separator className="flex-1 bg-border/50" />
        <span className="text-xs text-muted-foreground">
          存活 {aliveCount}/{gameState.players.length}
        </span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
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
  );
}
