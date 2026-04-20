import { useEffect, useRef, useState } from 'react'
import type { AxiosError } from 'axios'
import {
  ArrowLeft,
  Check,
  Crown,
  Eye,
  Play,
  Shield,
  Shuffle,
  Swords,
  User,
  Users,
} from 'lucide-react'
import { useGameStore } from '../store/gameStore'
import { roomApi, gameApi, type CharacterInfo } from '../api/client'
import type { RoomView as RoomViewType } from '../types/room'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

// ---- Static mode metadata. Mirrors backend config.GAME_MODES (3 classic
// modes). Kept in frontend because these don't change at runtime and would
// otherwise need an extra round-trip to populate the selector.
const GAME_MODES = [
  {
    value: 'classic_6_witch',
    label: '6人局 - 预言家+女巫',
    desc: '含预言家、女巫的经典阵容',
    icon: Eye,
  },
  {
    value: 'classic_6_hunter',
    label: '6人局 - 预言家+猎人',
    desc: '含预言家、猎人的经典阵容',
    icon: Swords,
  },
  {
    value: 'classic_6_guard',
    label: '6人局 - 预言家+守卫',
    desc: '含预言家、守卫的经典阵容',
    icon: Shield,
  },
] as const

// MAX_HUMANS is exported from types/room.ts. The chip uses room.players.length
// as the denominator (not MAX_HUMANS) per spec: `readyCount / playersInRoom`.

// Full 6-role roster. We always render the whole grid; anything not in the
// backend-provided room.role_options is rendered muted + disabled.
const ALL_ROLES = ['werewolf', 'seer', 'witch', 'hunter', 'guard', 'villager'] as const

const ROLE_LABELS: Record<string, { label: string; emoji: string }> = {
  werewolf: { label: '狼人', emoji: '🐺' },
  seer: { label: '预言家', emoji: '🔮' },
  witch: { label: '女巫', emoji: '🧪' },
  hunter: { label: '猎人', emoji: '🏹' },
  guard: { label: '守卫', emoji: '🛡️' },
  villager: { label: '村民', emoji: '👤' },
}

// Center-biased scrolling carousel copied from the pre-multiplayer landing
// page. Scales + fades edges as avatars slide past center. Gets its
// horizontal mask via inline style because Tailwind can't express it.
function AvatarCarousel({ avatars }: { avatars: string[] }) {
  const trackRef = useRef<HTMLDivElement>(null)
  const wrapRef = useRef<HTMLDivElement>(null)

  // Stabilize the animation-critical dep. RoomView re-renders every 2s
  // (from the room poll) and would otherwise pass a fresh array reference
  // on every tick, causing the useEffect to restart the rAF loop and
  // snap offset back to 0 — the "滚一点点就重置" bug.
  const avatarsKey = avatars.join('|')

  useEffect(() => {
    const track = trackRef.current
    const wrap = wrapRef.current
    if (!track || !wrap || avatars.length === 0) return

    let offset = 0
    let prev = performance.now()
    let raf = 0
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [avatarsKey])

  if (avatars.length === 0) return null
  // Triple the list so the wrap looks seamless.
  const items = [...avatars, ...avatars, ...avatars]

  return (
    <div
      ref={wrapRef}
      className="w-full max-w-md overflow-hidden"
      style={{
        maskImage:
          'linear-gradient(to right, transparent, black 12%, black 88%, transparent)',
        WebkitMaskImage:
          'linear-gradient(to right, transparent, black 12%, black 88%, transparent)',
      }}
    >
      <div
        ref={trackRef}
        className="flex items-center gap-4"
        style={{ willChange: 'transform' }}
      >
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

// Compact member-count chip in the header. Clicking expands a small panel
// listing members. User explicitly asked this NOT to be a full side panel.
function MemberChip({
  room,
  identity,
}: {
  room: RoomViewType
  identity: string
}) {
  const [open, setOpen] = useState(false)
  // Host is always considered "ready" — they own the start button, so there
  // is no ready toggle for them. Solo host thus reads as 1/1.
  const readyCount = room.players.filter((p) => p.is_host || p.ready).length
  const denom = room.players.length

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className={cn(
          'flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors',
          'border-amber-500/30 bg-amber-500/5 text-amber-200/90 hover:border-amber-500/50 hover:bg-amber-500/15',
        )}
        aria-expanded={open}
        aria-label="房间成员"
      >
        <Users className="size-3.5" />
        <span className="font-display tracking-wide">
          房间成员 {readyCount}/{denom}
        </span>
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-2 w-56 rounded-lg border border-amber-900/40 bg-card/95 p-2 shadow-[0_0_20px_rgba(0,0,0,0.4)] backdrop-blur">
          {room.players.length === 0 ? (
            <p className="text-muted-foreground/60 py-2 text-center text-xs italic">
              （空房间）
            </p>
          ) : (
            <ul className="space-y-1">
              {room.players.map((p) => (
                <li
                  key={p.identity}
                  className={cn(
                    'flex items-center gap-2 rounded-md px-2 py-1 text-xs',
                    p.identity === identity
                      ? 'bg-amber-500/10 text-amber-100'
                      : 'text-muted-foreground',
                  )}
                >
                  {p.is_host && <Crown className="size-3 shrink-0 text-amber-400" />}
                  <span className="truncate">{p.display_name}</span>
                  <span className="ml-auto flex items-center gap-1">
                    {p.identity === identity && (
                      <span className="rounded bg-amber-500/20 px-1 text-[10px] text-amber-200">
                        你
                      </span>
                    )}
                    {/* Host has no "ready" concept; they own the start button. */}
                    {!p.is_host &&
                      (p.ready ? (
                        <span className="flex items-center gap-0.5 rounded bg-emerald-500/20 px-1 text-[10px] text-emerald-300">
                          <Check className="size-2.5" /> 已准备
                        </span>
                      ) : (
                        <span className="rounded bg-muted/40 px-1 text-[10px] text-muted-foreground">
                          未准备
                        </span>
                      ))}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

interface RoomViewProps {
  roomId: string
}

/**
 * RoomView polls /api/rooms/{id} every 2s. When the poll sees `game_id` set,
 * it also fetches the game state so App.tsx can route to GameBoard.
 *
 * Visual layout mirrors the pre-multiplayer landing / CreateGameForm — gold
 * ornamental card, mode selector cards, role picker grid, character banner.
 * Member count is a compact chip in the header (not a full panel).
 */
export function RoomView({ roomId }: RoomViewProps) {
  const identity = useGameStore((s) => s.identity)
  const setCurrentRoomId = useGameStore((s) => s.setCurrentRoomId)
  const setGameState = useGameStore((s) => s.setGameState)
  const setPlayerId = useGameStore((s) => s.setPlayerId)

  const [room, setRoom] = useState<RoomViewType | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)
  const [leaving, setLeaving] = useState(false)
  const [roleLoading, setRoleLoading] = useState(false)
  const [modeLoading, setModeLoading] = useState(false)
  const [readyLoading, setReadyLoading] = useState(false)
  const [characters, setCharacters] = useState<CharacterInfo[]>([])

  // Transient join/ready/leave toasts (top-right). Diffed from the 2s poll.
  // Not using a toast library — 3-item FIFO with CSS fade.
  const [toasts, setToasts] = useState<{ id: number; message: string }[]>([])
  const toastIdRef = useRef(0)
  const pushToast = (message: string) => {
    const id = ++toastIdRef.current
    setToasts((prev) => [...prev, { id, message }].slice(-3))
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id))
    }, 2500)
  }

  // Fetch character pool once for the carousel. Failure is silent — banner
  // just stays empty, which is fine.
  useEffect(() => {
    let alive = true
    gameApi
      .getCharacters()
      .then((chars) => {
        if (alive) setCharacters(chars)
      })
      .catch((e) => console.warn('[RoomView] getCharacters failed:', e))
    return () => {
      alive = false
    }
  }, [])

  // Poll room state every 2s. On game_id appearance, fetch game state once
  // and the store update flips App.tsx into GameBoard mode.
  //
  // prevPlayersRef holds the last poll's players keyed by identity, so we
  // can emit toasts on join/leave/ready-toggle diffs. We skip toasts for
  // the viewer's own identity to avoid self-spam ("你已准备").
  const prevPlayersRef = useRef<Map<string, { display_name: string; ready: boolean; is_host: boolean }> | null>(null)
  useEffect(() => {
    let alive = true
    let gameFetched = false

    const poll = async () => {
      try {
        const r = await roomApi.get(roomId)
        if (!alive) return

        // Diff against the previous snapshot to emit transient toasts.
        // First successful poll seeds the map without emitting (avoids a
        // flood of "X 加入了房间" on mount).
        const curr = new Map(
          r.players.map((p) => [
            p.identity,
            { display_name: p.display_name, ready: p.ready, is_host: p.is_host },
          ]),
        )
        const prev = prevPlayersRef.current
        if (prev !== null) {
          // Joins
          for (const [id, info] of curr) {
            if (!prev.has(id) && id !== identity) {
              pushToast(`${info.display_name} 加入了房间`)
            }
          }
          // Leaves
          for (const [id, info] of prev) {
            if (!curr.has(id) && id !== identity) {
              pushToast(`${info.display_name} 离开了房间`)
            }
          }
          // Ready toggles (non-host only — host has no ready concept)
          for (const [id, info] of curr) {
            const prevInfo = prev.get(id)
            if (!prevInfo || info.is_host) continue
            if (prevInfo.ready !== info.ready && id !== identity) {
              pushToast(
                info.ready
                  ? `${info.display_name} 已准备`
                  : `${info.display_name} 取消了准备`,
              )
            }
          }
        }
        prevPlayersRef.current = curr

        setRoom(r)
        setLoading(false)

        // Game spawned — pull the first game state once, then let GameBoard
        // take over polling. Skip if the room has moved to FINISHED (host
        // hasn't restarted yet) — we don't want to drag users back into
        // GameBoard after they chose "返回房间".
        if (r.game_id && !gameFetched && r.status !== 'finished') {
          gameFetched = true
          try {
            const gs = await gameApi.getGameState(r.game_id, 'player_0')
            if (!alive) return
            // Find our slot by identity — backend now echoes identity on
            // human PlayerResponse entries.
            const mySlot = gs.players.find((p) => p.is_human && p.identity === identity)
            if (mySlot) {
              setPlayerId(mySlot.id)
              // Re-fetch with correct player_id so filtered events match.
              const gs2 = await gameApi.getGameState(r.game_id, mySlot.id)
              if (!alive) return
              setGameState(gs2)
            } else {
              // Fall back to first human slot (single-player legacy).
              const firstHuman = gs.players.find((p) => p.is_human)
              if (firstHuman) {
                setPlayerId(firstHuman.id)
                const gs2 = await gameApi.getGameState(r.game_id, firstHuman.id)
                if (!alive) return
                setGameState(gs2)
              } else {
                setGameState(gs)
              }
            }
          } catch (e) {
            console.error('[RoomView] getGameState failed:', e)
            gameFetched = false // allow retry on next poll
          }
        }

        // Room restarted — game_id cleared while we were in-game.
        if (!r.game_id) gameFetched = false
      } catch (err) {
        const axErr = err as AxiosError<{ detail?: { code?: string } }>
        const code = axErr.response?.data?.detail?.code
        if (code === 'ROOM_NOT_FOUND') {
          setError('房间不存在')
        } else {
          console.error('[RoomView] poll failed:', err)
        }
      }
    }

    poll()
    const timer = setInterval(poll, 2000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [roomId, identity, setGameState, setPlayerId])

  const me = room?.players.find((p) => p.identity === identity)
  const isHost = !!(room && me && room.host_identity === identity)

  const handleLeave = async () => {
    setLeaving(true)
    try {
      await roomApi.leave(roomId)
    } catch (e) {
      console.error('[RoomView] leave failed:', e)
    } finally {
      setCurrentRoomId(null)
      setLeaving(false)
    }
  }

  const handleStart = async () => {
    if (!room) return
    setStarting(true)
    setError('')
    try {
      await roomApi.start(roomId)
      // Poll loop will pick up game_id and bootstrap GameBoard.
    } catch (err) {
      const axErr = err as AxiosError<{ detail?: { message?: string } }>
      setError(axErr.response?.data?.detail?.message || '开始游戏失败')
      setStarting(false)
    }
  }

  const handleToggleRole = async (role: string | null) => {
    if (!room) return
    // Client-side gate: ready players must cancel ready before re-picking.
    // Backend also enforces this (SET_ROLE_FAILED), but skipping the request
    // here keeps the UI snappy and prevents a flash of error text.
    if (me?.ready) return
    setRoleLoading(true)
    setError('')
    try {
      const r = await roomApi.setRole(roomId, role)
      setRoom(r)
    } catch (err) {
      const axErr = err as AxiosError<{ detail?: { message?: string } }>
      setError(axErr.response?.data?.detail?.message || '设置身份失败')
    } finally {
      setRoleLoading(false)
    }
  }

  const handleSetMode = async (mode: string) => {
    if (!room || !isHost || mode === room.mode) return
    setModeLoading(true)
    setError('')
    try {
      // Backend auto-clears every player's ready flag on mode change; the
      // 2s poll will pick up the reset view.
      const r = await roomApi.setMode(roomId, mode)
      setRoom(r)
    } catch (err) {
      const axErr = err as AxiosError<{ detail?: { message?: string } }>
      setError(axErr.response?.data?.detail?.message || '切换模式失败')
    } finally {
      setModeLoading(false)
    }
  }

  const handleToggleReady = async () => {
    if (!room || !me) return
    setReadyLoading(true)
    setError('')
    try {
      const r = await roomApi.setReady(roomId, !me.ready)
      setRoom(r)
    } catch (err) {
      const axErr = err as AxiosError<{ detail?: { message?: string } }>
      setError(axErr.response?.data?.detail?.message || '切换准备状态失败')
    } finally {
      setReadyLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-muted-foreground text-sm">房间加载中...</p>
      </div>
    )
  }

  if (!room) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 p-6">
        <p className="text-red-400">{error || '无法加载房间'}</p>
        <Button onClick={() => setCurrentRoomId(null)} variant="outline">
          返回大厅
        </Button>
      </div>
    )
  }

  const isWaiting = room.status === 'waiting'
  const roleOptionSet = new Set(room.role_options)
  const avatarUrls = characters.map((c) => c.avatar_url)

  return (
    <div className="relative min-h-screen p-4 md:p-8">
      {/* Transient join/ready/leave toasts — FIFO, max 3, 2.5s auto-dismiss.
          Fixed top-right. Uses CSS transitions, no toast library. */}
      <div className="pointer-events-none fixed right-4 top-4 z-30 flex w-64 flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className="animate-in fade-in slide-in-from-right-2 rounded-md border border-amber-500/40 bg-amber-950/80 px-3 py-2 text-xs text-amber-100 shadow-[0_0_12px_rgba(212,165,116,0.15)] backdrop-blur duration-200"
          >
            {t.message}
          </div>
        ))}
      </div>
      <div className="mx-auto flex max-w-xl flex-col items-center">
        {/* Header: back button + room name + member chip */}
        <div className="mb-5 flex w-full items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Button
              onClick={handleLeave}
              disabled={leaving}
              size="sm"
              variant="outline"
              className="gap-1.5"
            >
              <ArrowLeft className="size-3.5" />
              {leaving ? '离开中...' : '离开房间'}
            </Button>
            <h1 className="font-display bg-gradient-to-r from-amber-200 to-amber-400 bg-clip-text text-2xl font-bold tracking-wide text-transparent">
              {room.name}
            </h1>
          </div>
          <MemberChip room={room} identity={identity} />
        </div>

        {/* Ornamental divider */}
        <div className="mb-4 flex items-center justify-center gap-2.5">
          <div className="h-px w-16 bg-gradient-to-r from-transparent to-amber-500/50" />
          <div className="size-1.5 rotate-45 bg-amber-400/60" />
          <div className="size-2.5 rotate-45 border border-amber-400/50" />
          <div className="size-1.5 rotate-45 bg-amber-400/60" />
          <div className="h-px w-16 bg-gradient-to-l from-transparent to-amber-500/50" />
        </div>

        {/* Character banner */}
        <div className="mb-6 w-full max-w-md">
          <AvatarCarousel avatars={avatarUrls} />
        </div>

        {/* Main setup card — mirrors the old CreateGameForm visual style */}
        <Card className="relative w-full max-w-md overflow-hidden border-amber-900/30 bg-card/80 shadow-[0_0_40px_rgba(212,165,116,0.08)] backdrop-blur-sm">
          {/* Gold ornamental top edge */}
          <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-amber-400/50 to-transparent" />
          <CardHeader className="text-center">
            <CardTitle className="font-display text-2xl tracking-wide text-amber-100/90">
              准备开局
            </CardTitle>
            <CardDescription>
              {isHost ? '作为房主,为这场冒险选择模式与身份' : '等待房主选择模式,你可以先挑选身份'}
            </CardDescription>
          </CardHeader>

          <CardContent>
            <div className="space-y-5">
              {/* --- Mode selector ---
                  Host: all 3 cards clickable (current mode ringed amber).
                  Non-host: same layout, but only the current mode card is
                  highlighted; the other two render at opacity-40 and are
                  non-interactive. Backend is the only source of truth for
                  mode changes. */}
              <div className="space-y-3">
                <label className="text-muted-foreground block text-sm font-medium">
                  游戏模式
                </label>
                <div className="space-y-2">
                  {GAME_MODES.map((gm) => {
                    const Icon = gm.icon
                    const isActive = room.mode === gm.value
                    const clickable = isHost && isWaiting && !modeLoading && !isActive
                    // Non-host's non-active cards: visibly dim.
                    const nonHostDimmed = !isHost && !isActive
                    return (
                      <button
                        key={gm.value}
                        type="button"
                        onClick={() => clickable && handleSetMode(gm.value)}
                        disabled={!isHost || !isWaiting || modeLoading}
                        className={cn(
                          'flex w-full items-center gap-3 rounded-lg border p-3 text-left transition-all',
                          isActive
                            ? 'border-amber-500/50 bg-amber-500/10 shadow-[0_0_12px_rgba(212,165,116,0.15)]'
                            : clickable
                              ? 'border-border hover:border-border/80 hover:bg-muted/50'
                              : nonHostDimmed
                                ? 'border-border/40 opacity-40 cursor-not-allowed'
                                : 'border-border/40 opacity-60 cursor-not-allowed',
                        )}
                      >
                        <div
                          className={cn(
                            'rounded-md p-2',
                            isActive
                              ? 'bg-amber-500/20 text-amber-400'
                              : 'bg-muted text-muted-foreground',
                          )}
                        >
                          <Icon className="size-5" />
                        </div>
                        <div>
                          <div
                            className={cn(
                              'text-sm font-medium',
                              isActive ? 'text-foreground' : 'text-muted-foreground',
                            )}
                          >
                            {gm.label}
                          </div>
                          <div className="text-muted-foreground text-xs">{gm.desc}</div>
                        </div>
                      </button>
                    )
                  })}
                </div>
                {!isHost && (
                  <p className="text-muted-foreground/60 text-center text-xs italic">
                    仅房主可切换模式
                  </p>
                )}
              </div>

              {/* --- Role picker (self only, while waiting) ---
                  Locked when me.ready — user must 取消准备 first. Backend
                  also rejects (SET_ROLE_FAILED) as a defense-in-depth check. */}
              {me && isWaiting && (
                <div className={cn('space-y-3', me.ready && 'pointer-events-none')}>
                  <label className="text-muted-foreground block text-sm font-medium">
                    <User className="mr-1.5 inline size-3.5" />
                    选择身份（可选,随机兜底）
                  </label>
                  <div
                    className={cn(
                      'grid grid-cols-3 gap-2',
                      me.ready && 'opacity-50',
                    )}
                  >
                    {/* Random option */}
                    <button
                      type="button"
                      onClick={() => handleToggleRole(null)}
                      disabled={roleLoading || me.ready}
                      className={cn(
                        'flex flex-col items-center gap-1.5 rounded-lg border p-3 transition-all',
                        me.ready && 'cursor-not-allowed',
                        me.preferred_role === null
                          ? 'border-amber-500/50 bg-amber-500/10 shadow-[0_0_12px_rgba(212,165,116,0.15)]'
                          : 'border-border hover:border-border/80 hover:bg-muted/50',
                      )}
                    >
                      <Shuffle
                        className={cn(
                          'size-5',
                          me.preferred_role === null
                            ? 'text-amber-400'
                            : 'text-muted-foreground',
                        )}
                      />
                      <span
                        className={cn(
                          'text-xs font-medium',
                          me.preferred_role === null
                            ? 'text-foreground'
                            : 'text-muted-foreground',
                        )}
                      >
                        随机
                      </span>
                    </button>

                    {/* Full 6-role grid. Roles outside the current mode's
                        role_options render muted + disabled. */}
                    {ALL_ROLES.map((role) => {
                      const info = ROLE_LABELS[role]
                      const isActive = me.preferred_role === role
                      const enabled = roleOptionSet.has(role)
                      return (
                        <button
                          key={role}
                          type="button"
                          onClick={() =>
                            enabled && handleToggleRole(isActive ? null : role)
                          }
                          disabled={!enabled || roleLoading || me.ready}
                          className={cn(
                            'flex flex-col items-center gap-1.5 rounded-lg border p-3 transition-all',
                            me.ready && 'cursor-not-allowed',
                            isActive
                              ? 'border-amber-500/50 bg-amber-500/10 shadow-[0_0_12px_rgba(212,165,116,0.15)]'
                              : enabled
                                ? 'border-border hover:border-border/80 hover:bg-muted/50'
                                : 'border-border/30 bg-muted/10 opacity-40',
                          )}
                          title={enabled ? undefined : '当前模式不包含此身份'}
                        >
                          <span className="text-lg">{info.emoji}</span>
                          <span
                            className={cn(
                              'text-xs font-medium',
                              isActive
                                ? 'text-foreground'
                                : 'text-muted-foreground',
                            )}
                          >
                            {info.label}
                          </span>
                        </button>
                      )
                    })}
                  </div>
                  {me.ready && (
                    <p className="text-center text-xs italic text-amber-300/70">
                      已准备,取消准备后才能改选
                    </p>
                  )}
                </div>
              )}

              {/* --- Viewer name chip --- */}
              {me && (
                <div className="flex items-center justify-center gap-2 text-xs">
                  <span className="text-muted-foreground">你的席位:</span>
                  <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2.5 py-0.5 font-semibold text-amber-200">
                    {me.display_name}
                  </span>
                  {isHost && (
                    <span className="flex items-center gap-1 rounded-full border border-amber-500/40 bg-amber-900/30 px-2 py-0.5 text-amber-300">
                      <Crown className="size-3" /> 房主
                    </span>
                  )}
                </div>
              )}

              {/* --- Game Info --- */}
              <div className="relative space-y-2.5 overflow-hidden rounded-lg border border-amber-900/25 bg-amber-950/20 p-4">
                <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-amber-500/30 to-transparent" />
                <p className="font-display text-sm tracking-wider text-amber-200/50">冒险须知</p>
                <ul className="text-muted-foreground space-y-2 text-[13px] leading-relaxed">
                  <li className="flex items-start gap-2.5">
                    <span className="mt-px shrink-0 text-amber-400/60">◆</span>
                    <span>与提瓦特各地的朋友们展开博弈,最多 6 位玩家共同参与</span>
                  </li>
                  <li className="flex items-start gap-2.5">
                    <span className="mt-px shrink-0 text-amber-400/60">◆</span>
                    <span>空缺的席位将由 AI 扮演 —— 他们会思考、会怀疑、会结盟,也会说谎</span>
                  </li>
                  <li className="flex items-start gap-2.5">
                    <span className="mt-px shrink-0 text-amber-400/60">◆</span>
                    <span>每一轮随机顺序发言,请在对话中寻找破绽,赢得最终胜利</span>
                  </li>
                </ul>
              </div>

              {/* --- Action button ---
                  Host: "开始游戏" with an advisory ready-count chip (non-host
                  players only — host has no ready flag of their own).
                  Non-host: ready/cancel-ready toggle. No start button. */}
              {isWaiting && isHost && (() => {
                const nonHosts = room.players.filter((p) => !p.is_host)
                const readyCount = nonHosts.filter((p) => p.ready).length
                // Gate start on everyone-non-host-ready. Solo host has
                // nonHosts.length === 0 so start is allowed immediately
                // (single-player non-regression).
                const allReady = readyCount === nonHosts.length
                return (
                  <Button
                    onClick={handleStart}
                    disabled={starting || room.players.length === 0 || !allReady}
                    size="lg"
                    className="w-full gap-2 bg-gradient-to-r from-amber-800 via-red-700 to-amber-800 font-semibold tracking-wide text-amber-50 shadow-[0_0_20px_rgba(212,165,116,0.12)] hover:from-amber-700 hover:via-red-600 hover:to-amber-700 disabled:opacity-60"
                  >
                    <Play className="size-4" />
                    {starting
                      ? '启动中...'
                      : nonHosts.length > 0
                        ? allReady
                          ? `开始游戏 · ${readyCount}/${nonHosts.length} 已准备`
                          : `等待全员准备 · ${readyCount}/${nonHosts.length}`
                        : '开始游戏'}
                  </Button>
                )
              })()}
              {isWaiting && !isHost && me && (
                <Button
                  onClick={handleToggleReady}
                  disabled={readyLoading}
                  size="lg"
                  variant={me.ready ? 'outline' : 'default'}
                  className={cn(
                    'w-full gap-2 font-semibold tracking-wide',
                    me.ready
                      ? 'border-amber-500/50 text-amber-200 hover:bg-amber-500/10'
                      : 'bg-gradient-to-r from-amber-800 via-red-700 to-amber-800 text-amber-50 shadow-[0_0_20px_rgba(212,165,116,0.12)] hover:from-amber-700 hover:via-red-600 hover:to-amber-700',
                  )}
                >
                  {me.ready ? (
                    <>
                      <Check className="size-4" /> 取消准备
                    </>
                  ) : (
                    <>
                      <Play className="size-4" /> 准备
                    </>
                  )}
                </Button>
              )}
              {room.status === 'starting' && (
                <p className="py-2 text-center text-sm text-amber-300">游戏准备中...</p>
              )}
              {room.status === 'in_progress' && (
                <p className="py-2 text-center text-sm text-red-300">
                  游戏进行中（即将加载）...
                </p>
              )}
              {room.status === 'finished' && isHost && (
                <Button
                  onClick={async () => {
                    try {
                      await roomApi.restart(roomId)
                      setGameState(null)
                    } catch (err) {
                      const axErr = err as AxiosError<{ detail?: { message?: string } }>
                      setError(axErr.response?.data?.detail?.message || '重开失败')
                    }
                  }}
                  size="lg"
                  className="w-full bg-gradient-to-r from-amber-800 via-red-700 to-amber-800 font-semibold tracking-wide text-amber-50 hover:from-amber-700 hover:via-red-600 hover:to-amber-700"
                >
                  再来一局
                </Button>
              )}
              {room.status === 'finished' && !isHost && (
                <p className="text-muted-foreground py-2 text-center text-sm">
                  等待房主重新开始...
                </p>
              )}

              {error && <p className="text-center text-sm text-red-400">{error}</p>}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
