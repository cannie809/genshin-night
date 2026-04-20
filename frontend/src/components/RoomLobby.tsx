import { useEffect, useState } from 'react'
import type { AxiosError } from 'axios'
import { Users, Crown, Edit2, Check, X, DoorOpen } from 'lucide-react'
import { useGameStore } from '../store/gameStore'
import { roomApi } from '../api/client'
import { MAX_HUMANS, type RoomView, type RoomStatus } from '../types/room'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const STATUS_LABEL: Record<RoomStatus, string> = {
  waiting: '等待中',
  starting: '准备中',
  in_progress: '进行中',
  finished: '已结束',
}

const STATUS_CLASS: Record<RoomStatus, string> = {
  waiting: 'bg-emerald-900/40 text-emerald-300 border-emerald-700/40',
  starting: 'bg-amber-900/40 text-amber-300 border-amber-700/40',
  in_progress: 'bg-red-900/40 text-red-300 border-red-700/40',
  finished: 'bg-slate-800/50 text-slate-300 border-slate-600/40',
}

const MAX_NAME_LEN = 16

// Key-based editor: bumping `editKey` remounts the input so the initial
// value is seeded from `displayName` without needing a setState-in-effect.
function NicknameEditor({
  initial,
  onSave,
  onCancel,
}: {
  initial: string
  onSave: (value: string) => void
  onCancel: () => void
}) {
  const [draft, setDraft] = useState(initial)
  const save = () => {
    const t = draft.trim()
    if (t && t.length <= MAX_NAME_LEN) onSave(t)
  }
  return (
    <div className="flex items-center gap-2">
      <input
        type="text"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') save()
          if (e.key === 'Escape') onCancel()
        }}
        maxLength={MAX_NAME_LEN}
        autoFocus
        className="bg-background/60 w-40 rounded-md border border-amber-500/30 px-3 py-1.5 text-sm focus:border-amber-400/60 focus:outline-none"
      />
      <button
        onClick={save}
        className="rounded-md p-1.5 text-emerald-300 hover:bg-emerald-900/30"
        aria-label="保存"
      >
        <Check className="size-4" />
      </button>
      <button
        onClick={onCancel}
        className="text-muted-foreground rounded-md p-1.5 hover:bg-muted/30"
        aria-label="取消"
      >
        <X className="size-4" />
      </button>
    </div>
  )
}

function NicknameBar() {
  const displayName = useGameStore((s) => s.displayName)
  const setDisplayName = useGameStore((s) => s.setDisplayName)
  const [editing, setEditing] = useState(false)

  if (editing) {
    return (
      <NicknameEditor
        initial={displayName ?? ''}
        onSave={(v) => {
          setDisplayName(v)
          setEditing(false)
        }}
        onCancel={() => setEditing(false)}
      />
    )
  }

  return (
    <div className="flex items-center gap-2">
      <span className="text-muted-foreground text-sm">当前昵称:</span>
      <span className="font-semibold text-amber-200">{displayName}</span>
      <button
        onClick={() => setEditing(true)}
        className="text-muted-foreground hover:text-foreground rounded-md p-1.5 hover:bg-muted/30"
        aria-label="修改昵称"
      >
        <Edit2 className="size-3.5" />
      </button>
    </div>
  )
}

export function RoomLobby() {
  const identity = useGameStore((s) => s.identity)
  const displayName = useGameStore((s) => s.displayName)
  const setCurrentRoomId = useGameStore((s) => s.setCurrentRoomId)

  const [rooms, setRooms] = useState<RoomView[]>([])
  const [loading, setLoading] = useState(true)
  const [joinError, setJoinError] = useState<{ roomId: string; message: string } | null>(null)
  const [joiningId, setJoiningId] = useState<string | null>(null)

  // Poll rooms every 3s
  useEffect(() => {
    let alive = true
    const fetchRooms = async () => {
      try {
        const list = await roomApi.list()
        if (!alive) return
        setRooms(list)
        setLoading(false)
      } catch (e) {
        console.error('[RoomLobby] list failed:', e)
      }
    }
    fetchRooms()
    const timer = setInterval(fetchRooms, 3000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  // Find the room the user is already a member of (any status). Allows
  // "回到我的房间" reconnect flow.
  const myRoom = rooms.find((r) => r.players.some((p) => p.identity === identity))

  const handleJoin = async (room: RoomView) => {
    if (!displayName) return
    setJoinError(null)
    setJoiningId(room.id)
    try {
      await roomApi.join(room.id, displayName)
      setCurrentRoomId(room.id)
    } catch (err) {
      const axErr = err as AxiosError<{ detail?: { code?: string; message?: string } }>
      const detail = axErr.response?.data?.detail
      const code = detail?.code
      let msg = '加入失败'
      if (code === 'ROOM_FULL') msg = '房间已满'
      else if (code === 'ROOM_IN_PROGRESS') msg = '房间游戏进行中'
      else if (code === 'ROOM_NOT_FOUND') msg = '房间不存在'
      else if (detail?.message) msg = detail.message
      setJoinError({ roomId: room.id, message: msg })
    } finally {
      setJoiningId(null)
    }
  }

  const handleReconnect = () => {
    if (myRoom) setCurrentRoomId(myRoom.id)
  }

  const isJoinable = (r: RoomView) =>
    r.status === 'waiting' || r.status === 'finished' || r.players.some((p) => p.identity === identity)

  return (
    <div className="min-h-screen p-4 md:p-8">
      <div className="mx-auto max-w-5xl">
        {/* Header */}
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <h1 className="font-display bg-gradient-to-r from-amber-200 to-amber-400 bg-clip-text text-2xl font-bold tracking-wide text-transparent md:text-3xl">
              房间大厅
            </h1>
          </div>
          <div className="flex items-center gap-4">
            <NicknameBar />
            {myRoom && (
              <Button
                size="sm"
                onClick={handleReconnect}
                className="gap-1.5 bg-amber-800 text-amber-50 hover:bg-amber-700"
              >
                <DoorOpen className="size-3.5" />
                回到我的房间 ({myRoom.name})
              </Button>
            )}
          </div>
        </div>

        {loading && (
          <div className="text-muted-foreground py-20 text-center text-sm">房间列表加载中...</div>
        )}

        {!loading && (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {rooms.map((r) => {
              const host = r.players.find((p) => p.identity === r.host_identity)
              const count = r.players.length
              const joinable = isJoinable(r)
              const joining = joiningId === r.id
              const iAmIn = r.players.some((p) => p.identity === identity)
              const thisError = joinError?.roomId === r.id ? joinError.message : null

              return (
                <Card
                  key={r.id}
                  className={cn(
                    'border-border/50 bg-card/60 space-y-3 p-4 backdrop-blur transition-all',
                    joinable && 'hover:border-amber-500/40 hover:shadow-[0_0_16px_rgba(212,165,116,0.08)]',
                    !joinable && 'opacity-70',
                  )}
                >
                  <div className="flex items-center justify-between">
                    <h3 className="font-display text-lg font-semibold tracking-wide text-amber-100">
                      {r.name}
                    </h3>
                    <span
                      className={cn(
                        'rounded-full border px-2 py-0.5 text-[11px] font-medium',
                        STATUS_CLASS[r.status],
                      )}
                    >
                      {STATUS_LABEL[r.status]}
                    </span>
                  </div>

                  <div className="text-muted-foreground flex items-center gap-2 text-sm">
                    <Users className="size-3.5" />
                    <span>
                      {count}/{MAX_HUMANS} 位玩家{count > 0 && host && (
                        <>
                          {' '}· 房主{' '}
                          <span className="text-amber-300">{host.display_name}</span>
                        </>
                      )}
                    </span>
                  </div>

                  {/* Member list */}
                  {count > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {r.players.map((p) => (
                        <span
                          key={p.identity}
                          className={cn(
                            'flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs',
                            p.identity === identity
                              ? 'border-amber-500/50 bg-amber-500/15 text-amber-200'
                              : 'border-border/50 bg-muted/40 text-muted-foreground',
                          )}
                        >
                          {p.is_host && <Crown className="size-2.5 text-amber-400" />}
                          {p.display_name}
                        </span>
                      ))}
                    </div>
                  )}

                  {count === 0 && (
                    <div className="text-muted-foreground/60 text-xs italic">（空房间）</div>
                  )}

                  <Button
                    onClick={() => handleJoin(r)}
                    disabled={!joinable || joining}
                    size="sm"
                    className={cn(
                      'w-full',
                      iAmIn
                        ? 'bg-amber-800 text-amber-50 hover:bg-amber-700'
                        : 'bg-emerald-800 text-emerald-50 hover:bg-emerald-700',
                    )}
                  >
                    {joining
                      ? '加入中...'
                      : iAmIn
                        ? '进入房间'
                        : r.status === 'in_progress'
                          ? '进行中'
                          : '加入房间'}
                  </Button>

                  {thisError && (
                    <p className="text-center text-xs text-red-400">{thisError}</p>
                  )}
                </Card>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
