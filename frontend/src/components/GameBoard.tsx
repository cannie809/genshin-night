import { useState, useEffect, useRef } from 'react'
import { Moon, RotateCw, X, RefreshCw, Trophy } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useGameStore } from '../store/gameStore'
import { gameApi } from '../api/client'
import { PhaseIndicator } from './PhaseIndicator'
import { PlayerGrid } from './PlayerGrid'
import { ActionPanel } from './ActionPanel'
import { GameLog } from './GameLog'
import { CreateGameForm } from './CreateGameForm'
import { PhaseTransition } from './PhaseTransition'
import { cn } from '@/lib/utils'

// Scrolling avatar carousel (center-big, edges-small)
function AvatarCarousel({ avatars }: { avatars: string[] }) {
  const trackRef = useRef<HTMLDivElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const track = trackRef.current
    const wrap = wrapRef.current
    if (!track || !wrap || avatars.length === 0) return

    let offset = 0
    let prev = performance.now()
    let raf: number
    const step = 72 // 56px avatar + 16px gap
    const total = avatars.length * step

    const tick = (now: number) => {
      const dt = (now - prev) / 1000
      prev = now
      offset = (offset + 28 * dt) % total

      const w = wrap.clientWidth
      const cx = w / 2
      track.style.transform = `translateX(${-offset}px)`

      const kids = track.children as HTMLCollectionOf<HTMLElement>
      for (let i = 0; i < kids.length; i++) {
        const x = i * step + step / 2 - offset
        const d = Math.abs(x - cx) / (w / 2)
        const s = Math.max(0.55, 1 - d * 0.45)
        const o = Math.max(0.2, 1 - d * 0.6)
        kids[i].style.transform = `scale(${s})`
        kids[i].style.opacity = `${o}`
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [avatars])

  if (avatars.length === 0) return null
  // Triple for seamless wrap
  const items = [...avatars, ...avatars, ...avatars]

  return (
    <div
      ref={wrapRef}
      className="w-full max-w-md overflow-hidden"
      style={{
        maskImage: 'linear-gradient(to right, transparent, black 12%, black 88%, transparent)',
        WebkitMaskImage:
          'linear-gradient(to right, transparent, black 12%, black 88%, transparent)',
      }}
    >
      <div ref={trackRef} className="flex items-center gap-4" style={{ willChange: 'transform' }}>
        {items.map((src, i) => (
          <img
            key={i}
            src={src}
            alt=""
            className="size-14 shrink-0 rounded-full border-2 border-amber-500/30 object-cover"
            style={{ willChange: 'transform, opacity' }}
            draggable={false}
          />
        ))}
      </div>
    </div>
  )
}

// Deterministic constellation stars
const STARS = Array.from({ length: 50 }, (_, i) => ({
  x: ((i * 37 + 13) % 97) + 1,
  y: ((i * 53 + 7) % 97) + 1,
  size: (i % 3) * 0.5 + 1,
  delay: ((i * 7) % 40) / 10,
  dur: (i % 3) + 2.5,
}))

// Deterministic floating particles
const PARTICLES = Array.from({ length: 8 }, (_, i) => ({
  x: ((i * 13 + 5) % 90) + 5,
  dur: 6 + (i % 4) * 2,
  delay: i * 1.5,
  opacity: 0.3 + (i % 3) * 0.15,
}))

const ROLE_EMOJI: Record<string, string> = {
  werewolf: '🐺',
  seer: '🔮',
  witch: '🧪',
  hunter: '🏹',
  guard: '🛡️',
  villager: '👤',
}

const ROLE_LABEL: Record<string, string> = {
  werewolf: '狼人',
  seer: '预言家',
  witch: '女巫',
  hunter: '猎人',
  guard: '守卫',
  villager: '村民',
}

export function GameBoard() {
  const { gameState, playerId } = useGameStore()
  const [selectedPlayerId, setSelectedPlayerId] = useState<string>()
  const [refreshing, setRefreshing] = useState(false)
  const [showWinnerOverlay, setShowWinnerOverlay] = useState(false)
  const [carouselAvatars, setCarouselAvatars] = useState<string[]>([])

  // Fetch character pool once for landing carousel
  useEffect(() => {
    if (!gameState) {
      gameApi
        .getCharacters()
        .then((chars) => {
          setCarouselAvatars(chars.map((c) => c.avatar_url))
        })
        .catch(() => {})
    }
  }, [!gameState])

  // Show winner overlay when game ends
  useEffect(() => {
    if (gameState?.winner) {
      setShowWinnerOverlay(true)
    }
  }, [gameState?.winner])

  // Clear selection when selected player becomes dead (prevents stale target)
  useEffect(() => {
    if (selectedPlayerId && gameState) {
      const player = gameState.players.find((p) => p.id === selectedPlayerId)
      if (player && !player.alive) {
        setSelectedPlayerId(undefined)
      }
    }
  }, [gameState?.players, selectedPlayerId])

  // Auto-refresh game state — fast (1.5s) when AI is speaking, normal (5s) otherwise
  useEffect(() => {
    if (!gameState) return

    const interval = gameState.ai_speaking ? 1500 : 5000
    const timer = setInterval(async () => {
      const current = useGameStore.getState().gameState
      if (!current) return // Game was reset, skip stale poll
      try {
        setRefreshing(true)
        const updated = await gameApi.getGameState(current.game_id, playerId)
        // Re-check after await — game might have been reset while waiting
        if (!useGameStore.getState().gameState) return
        useGameStore.getState().setGameState(updated)
      } catch (error) {
        console.error('Failed to refresh game state:', error)
      } finally {
        setRefreshing(false)
      }
    }, interval)

    return () => clearInterval(timer)
  }, [gameState?.game_id, gameState?.ai_speaking, playerId])

  // If game not created, show Genshin-themed landing page
  if (!gameState) {
    return (
      <div className="relative flex min-h-screen flex-col items-center justify-center overflow-hidden p-6">
        {/* Constellation star field */}
        <div className="pointer-events-none absolute inset-0">
          {STARS.map((s, i) => (
            <div
              key={i}
              className="absolute rounded-full bg-white/80"
              style={{
                left: `${s.x}%`,
                top: `${s.y}%`,
                width: s.size,
                height: s.size,
                animation: `twinkle ${s.dur}s ease-in-out ${s.delay}s infinite`,
              }}
            />
          ))}
        </div>

        {/* Floating amber particles */}
        <div className="pointer-events-none absolute inset-0 overflow-hidden">
          {PARTICLES.map((p, i) => (
            <div
              key={i}
              className="absolute rounded-full"
              style={{
                left: `${p.x}%`,
                bottom: -10,
                width: 3,
                height: 3,
                background: `rgba(212, 165, 116, ${p.opacity})`,
                animation: `drift-up ${p.dur}s linear ${p.delay}s infinite`,
              }}
            />
          ))}
        </div>

        {/* Title */}
        <div className="relative z-10 mb-5 text-center">
          <h1 className="font-display animate-title-glow bg-gradient-to-b from-amber-200 via-yellow-100 to-amber-300 bg-clip-text text-5xl font-bold tracking-[0.15em] text-transparent sm:text-6xl">
            狼人杀
          </h1>
          <p className="font-display mt-2 text-sm tracking-[0.3em] text-amber-300/45">
            月圆之夜 · 提瓦特的秘密
          </p>
        </div>

        {/* Ornamental divider */}
        <div className="relative z-10 mb-5 flex items-center justify-center gap-2.5">
          <div className="h-px w-16 bg-gradient-to-r from-transparent to-amber-500/50" />
          <div className="animate-pulse-glow size-1.5 rotate-45 bg-amber-400/60" />
          <div className="size-2.5 rotate-45 border border-amber-400/50" />
          <div className="animate-pulse-glow size-1.5 rotate-45 bg-amber-400/60" />
          <div className="h-px w-16 bg-gradient-to-l from-transparent to-amber-500/50" />
        </div>

        {/* Scrolling character carousel */}
        <div className="relative z-10 mb-7 w-full max-w-md">
          <AvatarCarousel avatars={carouselAvatars} />
        </div>

        <CreateGameForm />

        {/* Footer credit */}
        <p className="font-display relative z-10 mt-8 text-xs tracking-widest text-amber-200/20">
          AI 驱动的社交推理游戏
        </p>
      </div>
    )
  }

  return (
    <div className="min-h-screen p-4 md:p-8">
      <PhaseTransition />
      <div className="mx-auto max-w-7xl">
        {/* Header */}
        <div className="mb-6 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Moon className="text-accent size-7" />
            <h1 className="font-display bg-gradient-to-r from-red-500 to-purple-400 bg-clip-text text-3xl font-bold tracking-wide text-transparent">
              狼人杀
            </h1>
          </div>
          <div className="flex items-center gap-3">
            {gameState.winner && (
              <>
                <Button
                  onClick={() => setShowWinnerOverlay(true)}
                  size="sm"
                  variant="outline"
                  className="gap-1.5"
                >
                  <Trophy className="size-3.5" />
                  查看结算
                </Button>
                <Button
                  onClick={() => useGameStore.getState().reset()}
                  size="sm"
                  className="bg-accent/20 hover:bg-accent/30 text-accent border-accent/30 gap-1.5 border"
                >
                  <RefreshCw className="size-3.5" />
                  再来一局
                </Button>
              </>
            )}
            <div
              className={cn(
                'text-muted-foreground flex items-center gap-2 text-sm transition-opacity',
                refreshing ? 'opacity-100' : 'opacity-0',
              )}
            >
              <RotateCw className="size-3.5 animate-spin" />
              <span>刷新中</span>
            </div>
          </div>
        </div>

        <PhaseIndicator />

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <div className="space-y-6 lg:col-span-2">
            <PlayerGrid
              onPlayerSelect={(id) => {
                const player = gameState.players.find((p) => p.id === id)
                if (player?.alive) setSelectedPlayerId(id)
              }}
              selectedPlayerId={selectedPlayerId}
            />
            <ActionPanel selectedPlayerId={selectedPlayerId} />
          </div>

          <div className="lg:col-span-1">
            <GameLog />
          </div>
        </div>

        {/* Winner Overlay */}
        {showWinnerOverlay && gameState.winner && (
          <div className="animate-fade-in fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/80 py-8 backdrop-blur-sm">
            <div
              className={cn(
                'relative mx-4 max-w-lg rounded-2xl border-2 p-10 text-center',
                gameState.winner === 'werewolf'
                  ? 'border-red-700 bg-gradient-to-b from-red-950 to-red-900/80'
                  : 'border-blue-700 bg-gradient-to-b from-blue-950 to-blue-900/80',
              )}
            >
              {/* Close button */}
              <button
                onClick={() => setShowWinnerOverlay(false)}
                className="absolute top-4 right-4 rounded-lg p-1.5 text-white/40 transition-colors hover:bg-white/10 hover:text-white"
              >
                <X className="size-5" />
              </button>

              <div className="mb-3 text-5xl">{gameState.winner === 'werewolf' ? '🐺' : '🏘️'}</div>
              <h2 className="font-display mb-2 text-3xl font-bold tracking-wide">游戏结束</h2>
              <p
                className={cn(
                  'mb-5 text-xl font-semibold',
                  gameState.winner === 'werewolf' ? 'text-red-300' : 'text-blue-300',
                )}
              >
                {gameState.winner === 'werewolf' ? '🐺 狼人阵营' : '🏘️ 好人阵营'} 获胜
              </p>

              {/* All player roles reveal */}
              <div className="mb-6 text-left">
                <h3 className="mb-2 text-center text-sm font-semibold tracking-wider text-white/60">
                  身份揭晓
                </h3>
                <div className="grid grid-cols-2 gap-2">
                  {gameState.players.map((p) => (
                    <div
                      key={p.id}
                      className={cn(
                        'flex items-center gap-2 rounded-lg px-3 py-2 text-sm',
                        !p.alive && 'opacity-50',
                        p.role === 'werewolf'
                          ? 'border border-red-800/40 bg-red-900/40'
                          : 'border border-blue-800/30 bg-blue-900/30',
                      )}
                    >
                      <span>{ROLE_EMOJI[p.role] || '❓'}</span>
                      <span className={cn('font-medium', p.is_human && 'text-amber-300')}>
                        {p.name}
                      </span>
                      <span className="ml-auto text-xs text-white/50">
                        {ROLE_LABEL[p.role] || p.role}
                      </span>
                      {!p.alive && <span className="text-xs text-red-400">💀</span>}
                    </div>
                  ))}
                </div>
              </div>

              <Button
                onClick={() => setShowWinnerOverlay(false)}
                size="lg"
                className="border border-white/20 bg-white/10 font-semibold tracking-wide text-white hover:bg-white/20"
              >
                关闭
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
