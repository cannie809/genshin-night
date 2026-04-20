import { useState } from 'react'
import { useGameStore } from '../store/gameStore'

const MAX_LEN = 16

/**
 * First-run gate. No network call — backend is trust-the-client for identity,
 * so the nickname just persists to localStorage via the store.
 */
export function NicknameGate() {
  const setDisplayName = useGameStore((s) => s.setDisplayName)
  const [value, setValue] = useState('')
  const [error, setError] = useState('')

  const handleSubmit = () => {
    const trimmed = value.trim()
    if (!trimmed) {
      setError('昵称不能为空')
      return
    }
    if (trimmed.length > MAX_LEN) {
      setError(`昵称不能超过 ${MAX_LEN} 个字符`)
      return
    }
    setDisplayName(trimmed)
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <h1 className="font-display bg-gradient-to-b from-amber-200 via-yellow-100 to-amber-300 bg-clip-text text-4xl font-bold tracking-[0.15em] text-transparent">
            原神之夜
          </h1>
          <p className="font-display mt-2 text-sm tracking-[0.3em] text-amber-300/45">
            提瓦特的秘密
          </p>
        </div>

        <div className="bg-card/80 space-y-4 rounded-2xl border border-amber-500/20 p-6 backdrop-blur">
          <div>
            <label className="mb-2 block text-sm tracking-wide text-amber-200/60">
              输入你的昵称
            </label>
            <input
              type="text"
              value={value}
              onChange={(e) => {
                setValue(e.target.value)
                if (error) setError('')
              }}
              onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
              placeholder="例如：小明"
              maxLength={MAX_LEN}
              className="bg-background/60 text-foreground placeholder:text-muted-foreground/50 w-full rounded-lg border border-amber-500/30 px-4 py-3 transition-colors focus:border-amber-400/60 focus:outline-none"
              autoFocus
            />
            <p className="text-muted-foreground mt-1.5 text-xs">
              最多 {MAX_LEN} 个字符，可随时修改
            </p>
          </div>

          {error && <p className="text-sm text-red-400">{error}</p>}

          <button
            onClick={handleSubmit}
            disabled={!value.trim()}
            className="w-full rounded-lg border border-amber-500/40 bg-amber-500/20 py-3 font-medium tracking-wide text-amber-100 transition-colors hover:bg-amber-500/30 disabled:cursor-not-allowed disabled:opacity-50"
          >
            进入大厅
          </button>
        </div>
      </div>
    </div>
  )
}
