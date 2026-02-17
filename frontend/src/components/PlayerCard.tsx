import { useState } from 'react';
import { Skull, User, Eye, Crown, X } from 'lucide-react';
import type { Player } from '../types/game';
import { useGameStore } from '../store/gameStore';
import { StickerBubble } from './StickerBubble';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

interface PlayerCardProps {
  player: Player;
  isSelected?: boolean;
  onClick?: () => void;
  winner?: string | null;
}

const roleConfig: Record<string, {
  label: string;
  borderColor: string;
  glowColor: string;
  badgeClass: string;
}> = {
  werewolf: {
    label: '狼人',
    borderColor: 'border-werewolf/60',
    glowColor: 'shadow-[0_0_15px_rgba(220,38,38,0.2)]',
    badgeClass: 'bg-red-900/80 text-red-200 border-red-700/50',
  },
  seer: {
    label: '预言家',
    borderColor: 'border-seer/60',
    glowColor: 'shadow-[0_0_15px_rgba(168,85,247,0.2)]',
    badgeClass: 'bg-purple-900/80 text-purple-200 border-purple-700/50',
  },
  witch: {
    label: '女巫',
    borderColor: 'border-witch/60',
    glowColor: 'shadow-[0_0_15px_rgba(16,185,129,0.2)]',
    badgeClass: 'bg-emerald-900/80 text-emerald-200 border-emerald-700/50',
  },
  hunter: {
    label: '猎人',
    borderColor: 'border-hunter/60',
    glowColor: 'shadow-[0_0_15px_rgba(245,158,11,0.2)]',
    badgeClass: 'bg-amber-900/80 text-amber-200 border-amber-700/50',
  },
  guard: {
    label: '守卫',
    borderColor: 'border-cyan-500/60',
    glowColor: 'shadow-[0_0_15px_rgba(6,182,212,0.2)]',
    badgeClass: 'bg-cyan-900/80 text-cyan-200 border-cyan-700/50',
  },
  villager: {
    label: '村民',
    borderColor: 'border-villager/60',
    glowColor: 'shadow-[0_0_15px_rgba(59,130,246,0.2)]',
    badgeClass: 'bg-blue-900/80 text-blue-200 border-blue-700/50',
  },
  '???': {
    label: '未知',
    borderColor: 'border-border',
    glowColor: '',
    badgeClass: 'bg-muted text-muted-foreground border-border',
  },
  seer_wolf: {
    label: '狼人',
    borderColor: 'border-werewolf/60',
    glowColor: 'shadow-[0_0_15px_rgba(220,38,38,0.2)]',
    badgeClass: 'bg-red-900/80 text-red-200 border-red-700/50',
  },
  seer_good: {
    label: '好人',
    borderColor: 'border-villager/60',
    glowColor: 'shadow-[0_0_15px_rgba(59,130,246,0.2)]',
    badgeClass: 'bg-blue-900/80 text-blue-200 border-blue-700/50',
  },
};

const ALL_GUESS_OPTIONS = [
  { key: 'werewolf', label: '狼人' },
  { key: 'seer', label: '预言家' },
  { key: 'witch', label: '女巫' },
  { key: 'hunter', label: '猎人' },
  { key: 'guard', label: '守卫' },
  { key: 'villager', label: '村民' },
];

function getGuessOptions(mode: string) {
  return ALL_GUESS_OPTIONS.filter((opt) => {
    if (opt.key === 'witch') return mode.includes('witch');
    if (opt.key === 'hunter') return mode.includes('hunter');
    if (opt.key === 'guard') return mode.includes('guard');
    return true;
  });
}

/** Derive display role: seer check > guess > unknown */
function getDisplayRole(player: Player, guess?: string): string {
  // Known role (own role or game-end reveal)
  if (player.role !== '???') return player.role || '???';
  // Seer-checked: locked
  if (player.seer_result) {
    return player.seer_result === 'WEREWOLF' ? 'seer_wolf' : 'seer_good';
  }
  // Player guess: use for styling
  return guess || '???';
}

/** Whether the role badge should be interactive (guessable). */
function isGuessable(player: Player): boolean {
  return player.role === '???' && !player.seer_result;
}

export function PlayerCard({ player, isSelected, onClick, winner }: PlayerCardProps) {
  const { gameState, playerGuesses, setPlayerGuess, activeStickerBubbles } = useGameStore();
  const [expanded, setExpanded] = useState(false);
  const bubble = activeStickerBubbles.find((b) => b.playerName === player.name);

  const role = player.role || '???';
  const guess = playerGuesses[player.id];
  const guessOptions = getGuessOptions(gameState?.mode || '');
  const displayRole = getDisplayRole(player, guess);
  const config = roleConfig[displayRole] || roleConfig['???'];
  const guessable = isGuessable(player);

  const isWinner = winner
    ? (role === 'werewolf' && winner === 'werewolf') ||
      (role !== 'werewolf' && role !== '???' && winner === 'good')
    : null;

  return (
    <Card
      onClick={player.alive ? onClick : undefined}
      className={cn(
        "relative p-4 transition-all duration-200 border",
        player.alive && "cursor-pointer hover:scale-[1.02] hover:shadow-lg",
        player.alive ? config.borderColor : "border-border/30",
        player.alive && config.glowColor,
        isSelected && player.alive && "ring-2 ring-accent border-accent shadow-[0_0_20px_rgba(124,58,237,0.3)]",
        !player.alive && !isWinner && "opacity-50 grayscale pointer-events-none cursor-not-allowed",
        !player.alive && isWinner && "opacity-60 pointer-events-none cursor-not-allowed",
      )}
    >
      {/* Sticker bubble — anchored to Card top-right */}
      {bubble && <StickerBubble stickerUrl={bubble.stickerUrl} />}

      {/* Death overlay */}
      {!player.alive && (
        <div className="absolute inset-0 flex items-center justify-center rounded-xl z-10">
          <Skull className="size-10 text-muted-foreground/30" />
        </div>
      )}

      <div className={cn(!player.alive && "opacity-60")}>
        {/* Header: avatar + name + badge */}
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <div className="relative">
              {player.avatar_url ? (
                <img
                  src={player.avatar_url}
                  alt={player.name}
                  className="size-8 rounded-full object-cover border border-border/50"
                />
              ) : player.is_human ? (
                <User className="size-4 text-accent" />
              ) : (
                <div className="size-8 rounded-full bg-muted flex items-center justify-center text-xs text-muted-foreground border border-border/50">
                  AI
                </div>
              )}
              {isWinner && (
                <div className="absolute -top-1 -left-1 size-4 rounded-full flex items-center justify-center bg-amber-500 shadow-[0_0_6px_rgba(245,158,11,0.5)]">
                  <Crown className="size-2.5 text-white" />
                </div>
              )}
            </div>
            <span className="font-semibold text-sm">{player.name}</span>
          </div>
          {!player.is_human && (
            <Badge variant="outline" className="text-[10px] h-5 bg-muted/50 text-muted-foreground border-border/50">
              AI
            </Badge>
          )}
          {player.is_human && (
            <Badge variant="outline" className="text-[10px] h-5 bg-accent/10 text-accent border-accent/30">
              你
            </Badge>
          )}
        </div>

        {/* Role badge area */}
        <div className="flex items-center justify-between">
          <div className="flex-1 min-w-0">
            {expanded ? (
              /* Expanded: show role options */
              <div
                className="flex flex-wrap gap-1"
                onClick={(e) => e.stopPropagation()}
              >
                {guessOptions.map((opt) => {
                  const optConfig = roleConfig[opt.key];
                  const isActive = guess === opt.key;
                  return (
                    <button
                      key={opt.key}
                      onClick={(e) => {
                        e.stopPropagation();
                        setPlayerGuess(player.id, isActive ? null : opt.key);
                        setExpanded(false);
                      }}
                      className={cn(
                        "text-[10px] px-1.5 py-0.5 rounded border transition-all",
                        isActive
                          ? cn(optConfig.badgeClass, "ring-1 ring-white/30")
                          : cn(optConfig.badgeClass, "opacity-60 hover:opacity-100"),
                      )}
                    >
                      {opt.label}
                    </button>
                  );
                })}
                {/* Clear / close */}
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    if (guess) setPlayerGuess(player.id, null);
                    setExpanded(false);
                  }}
                  className="text-[10px] px-1 py-0.5 rounded border border-border/50 text-muted-foreground/60 hover:text-muted-foreground transition-colors"
                >
                  <X className="size-3" />
                </button>
              </div>
            ) : guessable ? (
              /* Collapsed & guessable: clickable badge */
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setExpanded(true);
                }}
                className="group"
              >
                <Badge
                  variant="outline"
                  className={cn(
                    "text-xs border cursor-pointer transition-all group-hover:brightness-125",
                    guess ? roleConfig[guess]?.badgeClass : config.badgeClass,
                  )}
                >
                  {guess ? `猜测：${roleConfig[guess]?.label}` : '未知'}
                </Badge>
              </button>
            ) : (
              /* Locked: seer-confirmed or known role */
              <Badge variant="outline" className={cn("text-xs border", config.badgeClass)}>
                {displayRole.startsWith('seer_') && <Eye className="size-3 mr-1 inline" />}
                {config.label}
              </Badge>
            )}
          </div>
          <span className={cn(
            "text-xs shrink-0 ml-2",
            player.alive ? "text-emerald-400" : "text-red-400"
          )}>
            {player.alive ? '存活' : '死亡'}
          </span>
        </div>
      </div>
    </Card>
  );
}
