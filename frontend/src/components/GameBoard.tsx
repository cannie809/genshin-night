import { useState, useEffect } from 'react'
import { Moon, RotateCw, X, RefreshCw, Trophy, ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useGameStore } from '../store/gameStore'
import { gameApi, roomApi } from '../api/client'
import { PhaseIndicator } from './PhaseIndicator'
import { PlayerGrid } from './PlayerGrid'
import { ActionPanel } from './ActionPanel'
import { GameLog } from './GameLog'
import { PhaseTransition } from './PhaseTransition'
import { cn } from '@/lib/utils'

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
  const gameState = useGameStore((s) => s.gameState)
  const playerId = useGameStore((s) => s.playerId)
  const currentRoomId = useGameStore((s) => s.currentRoomId)
  const setCurrentRoomId = useGameStore((s) => s.setCurrentRoomId)
  const setGameState = useGameStore((s) => s.setGameState)

  const [selectedPlayerId, setSelectedPlayerId] = useState<string>()
  const [refreshing, setRefreshing] = useState(false)
  const [showWinnerOverlay, setShowWinnerOverlay] = useState(false)
  const [returningLobby, setReturningLobby] = useState(false)

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

  if (!gameState) return null

  const handleReturnLobby = async () => {
    setReturningLobby(true)
    try {
      if (currentRoomId) {
        await roomApi.leave(currentRoomId)
      }
    } catch (err) {
      console.error('[GameBoard] leave failed:', err)
    } finally {
      setGameState(null)
      setCurrentRoomId(null)
      setReturningLobby(false)
    }
  }

  // "返回房间" is a passive drop-back into RoomView for everyone (host
  // and guest alike). The room stays in FINISHED until whoever is the
  // current host clicks 再来一局 from RoomView. If the original host left
  // via 返回大厅, the next-earliest member is auto-promoted to host by
  // the backend's leave_room logic — so a guest returning later can
  // correctly see the host controls and restart the room themselves.
  const handleReturnRoom = () => {
    setGameState(null)
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
              原神之夜
            </h1>
          </div>
          <div className="flex items-center gap-3">
            {/* Winner-only controls — HIDDEN entirely pre-winner (per spec) */}
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
                {/* 返回房间 — amber/primary style. For host, this also
                    restarts the room; for guest it's a passive return. */}
                <Button
                  onClick={handleReturnRoom}
                  size="sm"
                  className="gap-1.5 border border-amber-500/40 bg-amber-500/15 text-amber-100 hover:bg-amber-500/25"
                >
                  <RefreshCw className="size-3.5" />
                  返回房间
                </Button>
                <Button
                  onClick={handleReturnLobby}
                  disabled={returningLobby}
                  size="sm"
                  className="gap-1.5 border border-slate-500/30 bg-slate-500/10 text-slate-300 hover:bg-slate-500/20"
                >
                  <ArrowLeft className="size-3.5" />
                  返回大厅
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
                        {p.display_name || p.name}
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
