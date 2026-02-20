import { useState } from 'react'
import { useGameStore } from '../store/gameStore'
import { gameApi } from '../api/client'

export function AccessKeyGate() {
  const [inputKey, setInputKey] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const { setAccessKey, setPlayerName } = useGameStore()

  const handleSubmit = async () => {
    if (!inputKey.trim()) return
    setError('')
    setLoading(true)

    // Temporarily set the key so the interceptor picks it up
    setAccessKey(inputKey.trim())

    try {
      const result = await gameApi.verifyKey()
      setPlayerName(result.player_name)
    } catch {
      setAccessKey(null)
      setPlayerName(null)
      setError('密钥无效，请检查')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <h1 className="font-display bg-gradient-to-b from-amber-200 via-yellow-100 to-amber-300 bg-clip-text text-4xl font-bold tracking-[0.15em] text-transparent">
            狼人杀
          </h1>
          <p className="font-display mt-2 text-sm tracking-[0.3em] text-amber-300/45">
            月圆之夜 · 提瓦特的秘密
          </p>
        </div>

        <div className="bg-card/80 space-y-4 rounded-2xl border border-amber-500/20 p-6 backdrop-blur">
          <div>
            <label className="mb-2 block text-sm tracking-wide text-amber-200/60">访问密钥</label>
            <input
              type="password"
              value={inputKey}
              onChange={(e) => setInputKey(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
              placeholder="请输入密钥"
              className="bg-background/60 text-foreground placeholder:text-muted-foreground/50 w-full rounded-lg border border-amber-500/30 px-4 py-3 transition-colors focus:border-amber-400/60 focus:outline-none"
              autoFocus
            />
          </div>

          {error && <p className="text-sm text-red-400">{error}</p>}

          <button
            onClick={handleSubmit}
            disabled={loading || !inputKey.trim()}
            className="w-full rounded-lg border border-amber-500/40 bg-amber-500/20 py-3 font-medium tracking-wide text-amber-100 transition-colors hover:bg-amber-500/30 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? '验证中...' : '进入游戏'}
          </button>
        </div>
      </div>
    </div>
  )
}
