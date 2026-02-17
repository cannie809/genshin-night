import { Moon, Sun, Vote, Crosshair, Eye, Sparkles, Shield } from 'lucide-react';
import { useGameStore } from '../store/gameStore';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

const phaseConfig: Record<string, {
  label: string;
  icon: typeof Moon;
  gradient: string;
  textColor: string;
  badgeClass: string;
}> = {
  NIGHT_GUARD: {
    label: '守卫守护',
    icon: Shield,
    gradient: 'from-blue-950 via-blue-900/80 to-blue-950',
    textColor: 'text-blue-200',
    badgeClass: 'bg-blue-900/60 text-blue-300 border-blue-700/50',
  },
  NIGHT_WEREWOLF: {
    label: '狼人行动',
    icon: Moon,
    gradient: 'from-red-950 via-red-900/80 to-red-950',
    textColor: 'text-red-200',
    badgeClass: 'bg-red-900/60 text-red-300 border-red-700/50',
  },
  NIGHT_SEER: {
    label: '预言家查验',
    icon: Eye,
    gradient: 'from-purple-950 via-purple-900/80 to-purple-950',
    textColor: 'text-purple-200',
    badgeClass: 'bg-purple-900/60 text-purple-300 border-purple-700/50',
  },
  NIGHT_WITCH: {
    label: '女巫行动',
    icon: Sparkles,
    gradient: 'from-emerald-950 via-emerald-900/80 to-emerald-950',
    textColor: 'text-emerald-200',
    badgeClass: 'bg-emerald-900/60 text-emerald-300 border-emerald-700/50',
  },
  DAY_DISCUSSION: {
    label: '白天讨论',
    icon: Sun,
    gradient: 'from-amber-950 via-amber-900/80 to-amber-950',
    textColor: 'text-amber-200',
    badgeClass: 'bg-amber-900/60 text-amber-300 border-amber-700/50',
  },
  DAY_VOTE: {
    label: '投票阶段',
    icon: Vote,
    gradient: 'from-orange-950 via-orange-900/80 to-orange-950',
    textColor: 'text-orange-200',
    badgeClass: 'bg-orange-900/60 text-orange-300 border-orange-700/50',
  },
  HUNTER_SHOOT: {
    label: '猎人开枪',
    icon: Crosshair,
    gradient: 'from-amber-950 via-orange-900/80 to-amber-950',
    textColor: 'text-orange-200',
    badgeClass: 'bg-orange-900/60 text-orange-300 border-orange-700/50',
  },
  GAME_END: {
    label: '游戏结束',
    icon: Sun,
    gradient: 'from-gray-950 via-gray-900/80 to-gray-950',
    textColor: 'text-gray-200',
    badgeClass: 'bg-gray-800/60 text-gray-300 border-gray-600/50',
  },
  CHECK_VICTORY: {
    label: '结算中',
    icon: Sun,
    gradient: 'from-gray-950 via-gray-900/80 to-gray-950',
    textColor: 'text-gray-200',
    badgeClass: 'bg-gray-800/60 text-gray-300 border-gray-600/50',
  },
};

// Map night phases to the role that acts during that phase
const nightPhaseRole: Record<string, string> = {
  NIGHT_WEREWOLF: 'werewolf',
  NIGHT_SEER: 'seer',
  NIGHT_WITCH: 'witch',
  NIGHT_GUARD: 'guard',
};

const genericNightConfig = {
  label: '夜间行动中',
  icon: Moon,
  gradient: 'from-slate-950 via-slate-900/80 to-slate-950',
  textColor: 'text-slate-200',
  badgeClass: 'bg-slate-900/60 text-slate-300 border-slate-700/50',
};

export function PhaseIndicator() {
  const { gameState, playerId } = useGameStore();

  if (!gameState) return null;

  const phase = gameState.phase || '';
  const isNight = phase.startsWith('NIGHT_');

  // For night phases, only show the specific banner if it's the human player's own role acting.
  // Otherwise show a generic "夜间行动中" to avoid leaking which roles are still alive.
  let config;
  if (isNight) {
    const humanPlayer = gameState.players.find((p) => p.id === playerId);
    const actingRole = nightPhaseRole[phase];
    const isMyPhase = humanPlayer && actingRole && humanPlayer.role === actingRole;
    config = isMyPhase ? phaseConfig[phase] : genericNightConfig;
  } else {
    config = phaseConfig[phase] || {
      label: phase,
      icon: Moon,
      gradient: 'from-gray-900 via-gray-800 to-gray-900',
      textColor: 'text-gray-300',
      badgeClass: 'bg-gray-800 text-gray-300 border-gray-600',
    };
  }

  const Icon = config.icon;

  return (
    <div className="mb-6 animate-fade-in">
      <div className={cn(
        "relative rounded-xl overflow-hidden border border-border/30",
        `bg-gradient-to-r ${config.gradient}`,
      )}>
        {/* Atmospheric overlay */}
        {isNight && (
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_50%,_rgba(124,58,237,0.1)_0%,_transparent_50%)]" />
        )}

        <div className="relative flex items-center justify-between p-4 md:p-5">
          <div className="flex items-center gap-3">
            <div className={cn(
              "p-2 rounded-lg",
              isNight ? "bg-white/5" : "bg-white/10"
            )}>
              <Icon className={cn("size-6", config.textColor)} />
            </div>
            <div>
              <div className={cn("text-xl md:text-2xl font-display font-bold tracking-wide", config.textColor)}>
                {config.label}
              </div>
            </div>
          </div>

          <Badge variant="outline" className={cn("text-sm border px-3 py-1", config.badgeClass)}>
            第 {gameState.round_number} 轮
          </Badge>
        </div>
      </div>
    </div>
  );
}
