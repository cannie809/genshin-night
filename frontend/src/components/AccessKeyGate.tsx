import { useState } from 'react';
import { useGameStore } from '../store/gameStore';
import { gameApi } from '../api/client';

export function AccessKeyGate() {
  const [inputKey, setInputKey] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const { setAccessKey, setPlayerName } = useGameStore();

  const handleSubmit = async () => {
    if (!inputKey.trim()) return;
    setError('');
    setLoading(true);

    // Temporarily set the key so the interceptor picks it up
    setAccessKey(inputKey.trim());

    try {
      const result = await gameApi.verifyKey();
      setPlayerName(result.player_name);
    } catch {
      setAccessKey(null);
      setPlayerName(null);
      setError('密钥无效，请检查');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="text-4xl font-display font-bold tracking-[0.15em] bg-gradient-to-b from-amber-200 via-yellow-100 to-amber-300 bg-clip-text text-transparent">
            狼人杀
          </h1>
          <p className="mt-2 text-amber-300/45 text-sm tracking-[0.3em] font-display">
            月圆之夜 · 提瓦特的秘密
          </p>
        </div>

        <div className="bg-card/80 backdrop-blur border border-amber-500/20 rounded-2xl p-6 space-y-4">
          <div>
            <label className="block text-sm text-amber-200/60 mb-2 tracking-wide">
              访问密钥
            </label>
            <input
              type="password"
              value={inputKey}
              onChange={(e) => setInputKey(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
              placeholder="请输入密钥"
              className="w-full px-4 py-3 bg-background/60 border border-amber-500/30 rounded-lg text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:border-amber-400/60 transition-colors"
              autoFocus
            />
          </div>

          {error && (
            <p className="text-sm text-red-400">{error}</p>
          )}

          <button
            onClick={handleSubmit}
            disabled={loading || !inputKey.trim()}
            className="w-full py-3 bg-amber-500/20 hover:bg-amber-500/30 border border-amber-500/40 rounded-lg text-amber-100 font-medium tracking-wide transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? '验证中...' : '进入游戏'}
          </button>
        </div>
      </div>
    </div>
  );
}
