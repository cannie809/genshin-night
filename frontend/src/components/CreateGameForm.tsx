import { useState } from 'react';
import { Swords, Shield, Eye, Shuffle, User } from 'lucide-react';
import { gameApi } from '../api/client';
import { useGameStore } from '../store/gameStore';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';

const GAME_MODES = [
  {
    value: 'classic_6_witch',
    label: '6人局 - 预言家+女巫',
    desc: '含预言家、女巫的经典阵容',
    icon: Eye,
    roles: ['werewolf', 'seer', 'witch', 'villager'],
  },
  {
    value: 'classic_6_hunter',
    label: '6人局 - 预言家+猎人',
    desc: '含预言家、猎人的经典阵容',
    icon: Swords,
    roles: ['werewolf', 'seer', 'hunter', 'villager'],
  },
  {
    value: 'classic_6_guard',
    label: '6人局 - 预言家+守卫',
    desc: '含预言家、守卫的经典阵容',
    icon: Shield,
    roles: ['werewolf', 'seer', 'guard', 'villager'],
  },
];

const ROLE_LABELS: Record<string, { label: string; emoji: string }> = {
  werewolf: { label: '狼人', emoji: '🐺' },
  seer: { label: '预言家', emoji: '🔮' },
  witch: { label: '女巫', emoji: '🧪' },
  hunter: { label: '猎人', emoji: '🏹' },
  guard: { label: '守卫', emoji: '🛡️' },
  villager: { label: '村民', emoji: '👤' },
};

export function CreateGameForm() {
  const { setGameState, setPlayerId, setLastGameConfig, lastMode, lastPreferredRole } = useGameStore();
  const [mode, setMode] = useState(lastMode);
  const modeRoles = GAME_MODES.find((m) => m.value === lastMode)?.roles ?? [];
  const [preferredRole, setPreferredRole] = useState<string | null>(
    lastPreferredRole && modeRoles.includes(lastPreferredRole) ? lastPreferredRole : null
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const currentMode = GAME_MODES.find((m) => m.value === mode)!;
  const availableRoles = currentMode.roles;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const gameState = await gameApi.startGame({
        mode,
        preferred_role: preferredRole,
      });
      setLastGameConfig(mode, preferredRole);
      setGameState(gameState);
      setPlayerId('player_0');
    } catch (err: any) {
      setError(`创建游戏失败: ${err.response?.data?.detail || err.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card className="max-w-md w-full border-amber-900/30 bg-card/80 backdrop-blur-sm shadow-[0_0_40px_rgba(212,165,116,0.08)] relative overflow-hidden">
      {/* Gold ornamental top edge */}
      <div className="absolute top-0 inset-x-0 h-px bg-gradient-to-r from-transparent via-amber-400/50 to-transparent" />
      <CardHeader className="text-center">
        <CardTitle className="text-2xl font-display tracking-wide text-amber-100/90">创建新游戏</CardTitle>
        <CardDescription>选择游戏模式，开始你在提瓦特的冒险</CardDescription>
      </CardHeader>

      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-5">
          {/* Game Mode Selection */}
          <div className="space-y-3">
            <label className="block text-sm font-medium text-muted-foreground">
              游戏模式
            </label>
            <div className="space-y-2">
              {GAME_MODES.map((gm) => {
                const Icon = gm.icon;
                const isActive = mode === gm.value;
                return (
                  <button
                    key={gm.value}
                    type="button"
                    onClick={() => {
                      setMode(gm.value);
                      // Reset role if not available in new mode
                      if (preferredRole && !gm.roles.includes(preferredRole)) {
                        setPreferredRole(null);
                      }
                    }}
                    className={`
                      w-full flex items-center gap-3 p-3 rounded-lg border transition-all text-left
                      ${isActive
                        ? 'border-amber-500/50 bg-amber-500/10 shadow-[0_0_12px_rgba(212,165,116,0.15)]'
                        : 'border-border hover:border-border/80 hover:bg-muted/50'
                      }
                    `}
                  >
                    <div className={`
                      p-2 rounded-md
                      ${isActive ? 'bg-amber-500/20 text-amber-400' : 'bg-muted text-muted-foreground'}
                    `}>
                      <Icon className="size-5" />
                    </div>
                    <div>
                      <div className={`font-medium text-sm ${isActive ? 'text-foreground' : 'text-muted-foreground'}`}>
                        {gm.label}
                      </div>
                      <div className="text-xs text-muted-foreground">{gm.desc}</div>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Role Selection */}
          <div className="space-y-3">
            <label className="block text-sm font-medium text-muted-foreground">
              <User className="size-3.5 inline mr-1.5" />
              选择身份
            </label>
            <div className="grid grid-cols-3 gap-2">
              {/* Random option */}
              <button
                type="button"
                onClick={() => setPreferredRole(null)}
                className={`
                  flex flex-col items-center gap-1.5 p-3 rounded-lg border transition-all
                  ${preferredRole === null
                    ? 'border-amber-500/50 bg-amber-500/10 shadow-[0_0_12px_rgba(212,165,116,0.15)]'
                    : 'border-border hover:border-border/80 hover:bg-muted/50'
                  }
                `}
              >
                <Shuffle className={`size-5 ${preferredRole === null ? 'text-amber-400' : 'text-muted-foreground'}`} />
                <span className={`text-xs font-medium ${preferredRole === null ? 'text-foreground' : 'text-muted-foreground'}`}>
                  随机
                </span>
              </button>

              {/* Role options */}
              {availableRoles.map((role) => {
                const info = ROLE_LABELS[role];
                const isActive = preferredRole === role;
                return (
                  <button
                    key={role}
                    type="button"
                    onClick={() => setPreferredRole(role)}
                    className={`
                      flex flex-col items-center gap-1.5 p-3 rounded-lg border transition-all
                      ${isActive
                        ? 'border-amber-500/50 bg-amber-500/10 shadow-[0_0_12px_rgba(212,165,116,0.15)]'
                        : 'border-border hover:border-border/80 hover:bg-muted/50'
                      }
                    `}
                  >
                    <span className="text-lg">{info.emoji}</span>
                    <span className={`text-xs font-medium ${isActive ? 'text-foreground' : 'text-muted-foreground'}`}>
                      {info.label}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Game Info */}
          <div className="relative border border-amber-900/25 bg-amber-950/20 p-4 rounded-lg space-y-2.5 overflow-hidden">
            <div className="absolute top-0 inset-x-0 h-px bg-gradient-to-r from-transparent via-amber-500/30 to-transparent" />
            <p className="text-sm text-amber-200/50 font-display tracking-wider">冒险须知</p>
            <ul className="text-[13px] space-y-2 text-muted-foreground leading-relaxed">
              <li className="flex items-start gap-2.5">
                <span className="text-amber-400/60 mt-px shrink-0">◆</span>
                <span>你将扮演旅行者，与提瓦特各地的朋友们展开博弈</span>
              </li>
              <li className="flex items-start gap-2.5">
                <span className="text-amber-400/60 mt-px shrink-0">◆</span>
                <span>其余五位角色均由 AI 扮演 —— 他们会思考、会怀疑、会结盟，也会说谎</span>
              </li>
              <li className="flex items-start gap-2.5">
                <span className="text-amber-400/60 mt-px shrink-0">◆</span>
                <span>每一轮随机顺序发言，请在对话中寻找破绽，赢得最终胜利</span>
              </li>
            </ul>
          </div>

          {error && (
            <div className="bg-destructive/10 border border-destructive/30 text-destructive-foreground p-3 rounded-lg text-sm">
              {error}
            </div>
          )}

          <Button
            type="submit"
            disabled={loading}
            size="lg"
            className="w-full bg-gradient-to-r from-amber-800 via-red-700 to-amber-800 hover:from-amber-700 hover:via-red-600 hover:to-amber-700 text-amber-50 font-semibold tracking-wide shadow-[0_0_20px_rgba(212,165,116,0.12)]"
          >
            {loading ? (
              <span className="flex items-center gap-2">
                <span className="size-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                创建中...
              </span>
            ) : (
              '开始游戏'
            )}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
