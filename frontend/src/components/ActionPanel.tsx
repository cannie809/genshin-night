import { useState, useEffect, useRef } from 'react'
import {
  Crosshair,
  Eye,
  Sparkles,
  MessageSquare,
  Vote,
  Moon,
  Sun,
  Target,
  AlertCircle,
  CheckCircle2,
  Heart,
  Skull,
  SkipForward,
  Ban,
  Shield,
  Hourglass,
} from 'lucide-react'
import { useGameStore } from '../store/gameStore'
import { gameApi } from '../api/client'
import { TRANSITION_DURATION } from './PhaseTransition'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'
import type { ActionResult, CallerNightIntentKind } from '../types/game'

interface ActionPanelProps {
  selectedPlayerId?: string
}

// Map night phase to the role that acts
const PHASE_ROLE: Record<string, string> = {
  NIGHT_GUARD: 'guard',
  NIGHT_WEREWOLF: 'werewolf',
  NIGHT_SEER: 'seer',
  NIGHT_WITCH: 'witch',
}

// Which `caller_night_intent.kind` each NIGHT_* phase expects. Used to
// decide whether to render the "intent locked, waiting on others" UI.
const PHASE_INTENT_KIND: Record<string, CallerNightIntentKind> = {
  NIGHT_GUARD: 'guard_protect',
  NIGHT_WEREWOLF: 'wolf_kill',
  NIGHT_SEER: 'seer_check',
  NIGHT_WITCH: 'witch_action',
}

const ROLE_LABEL: Record<string, string> = {
  werewolf: '狼人',
  seer: '预言家',
  witch: '女巫',
  hunter: '猎人',
  guard: '守卫',
  villager: '村民',
  '???': '未知',
}

const phaseTheme: Record<string, { bg: string; border: string }> = {
  NIGHT_GUARD: { bg: 'bg-blue-950/30', border: 'border-blue-900/30' },
  NIGHT_WEREWOLF: { bg: 'bg-red-950/30', border: 'border-red-900/30' },
  NIGHT_SEER: { bg: 'bg-purple-950/30', border: 'border-purple-900/30' },
  NIGHT_WITCH: { bg: 'bg-emerald-950/30', border: 'border-emerald-900/30' },
  DAY_DISCUSSION: { bg: 'bg-amber-950/20', border: 'border-amber-900/30' },
  DAY_VOTE: { bg: 'bg-orange-950/20', border: 'border-orange-900/30' },
  HUNTER_SHOOT: { bg: 'bg-orange-950/30', border: 'border-orange-900/30' },
}

export function ActionPanel({ selectedPlayerId }: ActionPanelProps) {
  const { gameState, playerId, allEventsRevealed } = useGameStore()
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState('')
  const [speechText, setSpeechText] = useState('')
  const [nightEnteredLocal, setNightEnteredLocal] = useState(false)
  const [dayEnteredLocal, setDayEnteredLocal] = useState(false)

  // Effective barrier-entered flags: union of local "button was clicked and
  // animation played" and server-truth "caller has recorded this round's ack".
  // The server-truth half is what recovers the UI after a page reload — if
  // `caller_morning_acked` is already true, we never show the 进入白天 button
  // again for this round. Derived inline (no effect + setState) to satisfy
  // react-hooks/set-state-in-effect.
  // Dead observers don't drive phase transitions — their ack is filtered
  // out of the quorum. Forcing both flags true here skips the "进入白天/
  // 进入夜晚" passive waiting rows so the panel goes straight to the
  // observer views ("AI 发言中..." / "观战中：夜晚行动中"), which is what
  // actually reflects the game state when alive humans have already
  // advanced past the barrier.
  const _effectiveIsDeadObserver =
    !!gameState && !gameState.players.find((p) => p.id === playerId)?.alive &&
    gameState.players.some((p) => p.is_human && p.alive)
  const dayEntered =
    dayEnteredLocal || !!gameState?.caller_morning_acked || _effectiveIsDeadObserver
  const nightEntered =
    nightEnteredLocal || !!gameState?.caller_night_acked || _effectiveIsDeadObserver
  const setDayEntered = setDayEnteredLocal
  const setNightEntered = setNightEnteredLocal

  // Auto-advance through night phases that aren't the player's turn
  // (e.g., player is werewolf → skip witch/seer phases automatically)
  const nightAutoAdvRef = useRef(false)
  useEffect(() => {
    const phase = gameState?.phase ?? ''
    const currentPlayer = gameState?.players.find((p) => p.id === playerId)
    const isNight = phase.startsWith('NIGHT_')
    const isMyNightTurn =
      isNight && PHASE_ROLE[phase] === currentPlayer?.role && !!currentPlayer?.alive

    if (!nightEntered || !isNight || isMyNightTurn || !gameState || !playerId) {
      nightAutoAdvRef.current = false
      return
    }
    if (nightAutoAdvRef.current) return
    nightAutoAdvRef.current = true

    ;(async () => {
      try {
        setLoading(true)
        const updated = await gameApi.advanceNight({
          game_id: gameState.game_id,
          player_id: playerId,
        })
        useGameStore.getState().setGameState(updated)
        setWolfDiscussed(false)
      } catch (error: any) {
        setMessage(`操作失败: ${error.response?.data?.detail || error.message}`)
      } finally {
        nightAutoAdvRef.current = false
        setLoading(false)
      }
    })()
  }, [nightEntered, gameState?.phase, playerId])

  // Auto-trigger AI hunter shoot ONLY when the hunter is an AI and the
  // viewer is a non-hunter. When the hunter is a human (including another
  // human player in a multi-human game), we must wait for THAT human to
  // click their own shoot/skip button — otherwise a non-hunter client
  // would silently resolve the hunter's shot, bypassing the human's choice.
  const hunterShootRef = useRef(false)
  useEffect(() => {
    if (!gameState || !playerId) return
    if (gameState.phase !== 'HUNTER_SHOOT') {
      hunterShootRef.current = false
      return
    }
    const player = gameState.players.find((p) => p.id === playerId)
    if (!player || player.role === 'hunter' || hunterShootRef.current) return

    const hunterEvent = gameState.events.find(
      (e) => e.type === 'hunter_death_vote' || e.type === 'hunter_death_night',
    )
    const hunterId = hunterEvent?.data?.hunter_id
    if (!hunterId) return
    // The hunter's slot — may be a human in multi-human games.
    const hunterSlot = gameState.players.find((p) => p.id === hunterId)
    if (!hunterSlot || hunterSlot.is_human) {
      // Human hunter: their own client will handle the shoot/skip UI.
      return
    }

    hunterShootRef.current = true
    gameApi
      .hunterShoot({
        game_id: gameState.game_id,
        player_id: hunterId,
        action_type: 'hunter_shoot',
      })
      .then(async () => {
        const updated = await gameApi.getGameState(gameState.game_id, playerId)
        useGameStore.getState().setGameState(updated)
      })
      .catch(() => {
        // Retry via polling — game state refresh will pick up the result
      })
  }, [gameState?.phase, playerId])

  // Reset transition flags when switching between day/night
  useEffect(() => {
    if (gameState?.phase.startsWith('DAY_')) {
      setNightEntered(false)
    } else if (gameState?.phase.startsWith('NIGHT_')) {
      setDayEntered(false)
    }
    // Reset guard info when leaving guard phase
    if (gameState?.phase !== 'NIGHT_GUARD') {
      setGuardInfo(null)
    }
  }, [gameState?.phase])

  // Track whether we have called advanceDiscussion for THIS round.
  // Reset on round change. Keyed by (game_id, round_number).
  const advancedKeyRef = useRef<string>('')
  const advanceDiscussion = async () => {
    if (!gameState) return
    try {
      const updated = await gameApi.advanceDiscussion({
        game_id: gameState.game_id,
        player_id: playerId,
      })
      useGameStore.getState().setGameState(updated)
    } catch (error: any) {
      console.error('[ActionPanel] advanceDiscussion failed:', error)
    }
  }

  // Auto-call advanceDiscussion when entering DAY_DISCUSSION (after dayEntered)
  // AND after morning acks are complete. The backend is idempotent so retries
  // on poll are safe.
  useEffect(() => {
    if (!gameState) return
    if (gameState.phase !== 'DAY_DISCUSSION') {
      advancedKeyRef.current = ''
      return
    }
    if (!dayEntered) return
    // Still waiting for morning acks — don't advance yet.
    if (gameState.morning_acks < gameState.morning_acks_needed) return
    const key = `${gameState.game_id}:${gameState.round_number}:init`
    if (advancedKeyRef.current === key) return
    advancedKeyRef.current = key
    void advanceDiscussion()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    gameState?.phase,
    gameState?.round_number,
    gameState?.morning_acks,
    gameState?.morning_acks_needed,
    dayEntered,
  ])

  const [voteSubmitted, setVoteSubmitted] = useState(false)
  // Reset the local "I voted" flag at the start of each new vote phase.
  useEffect(() => {
    if (gameState?.phase !== 'DAY_VOTE') {
      setVoteSubmitted(false)
    }
  }, [gameState?.phase, gameState?.round_number])

  const [witchInfo, setWitchInfo] = useState<{
    victim_name: string | null
    save_available: boolean
    poison_available: boolean
  } | null>(null)
  const [witchLoading, setWitchLoading] = useState(false)
  const [guardInfo, setGuardInfo] = useState<{
    last_guarded_name: string | null
  } | null>(null)
  const [guardLoading, setGuardLoading] = useState(false)
  const [wolfDiscussed, setWolfDiscussed] = useState(false)
  const [wolfMessage, setWolfMessage] = useState('')

  // Latest Option B pending response for the caller. Populated when a
  // night-action call returns `{data:{pending:true}}` — server-side the
  // intent is stored, and polling's `caller_night_intent` will confirm it.
  // We keep this in local state (not just rely on caller_night_intent) so
  // we can render `waiting_for` NAMES (caller_night_intent only carries
  // target_id + kind + witch detail; the list of missing humans only
  // arrives on the pending POST response).
  const [pendingWaitingFor, setPendingWaitingFor] = useState<string[]>([])

  // Clear local pending banner when the caller's intent goes back to null
  // (quorum replayed → phase advanced, or round changed). Using
  // `subscribe()` keeps the setState call inside a callback rather than
  // an effect body — complies with the project's setState-in-effect rule.
  useEffect(() => {
    const unsub = useGameStore.subscribe((state, prev) => {
      const prevIntent = prev.gameState?.caller_night_intent ?? null
      const nextIntent = state.gameState?.caller_night_intent ?? null
      if (prevIntent && !nextIntent) {
        setPendingWaitingFor([])
      }
    })
    return unsub
  }, [])

  if (!gameState) return null

  const currentPlayer = gameState.players.find((p) => p.id === playerId)
  if (!currentPlayer) return null

  const phase = gameState.phase
  const isNight = phase.startsWith('NIGHT_')
  const isMyNightTurn = isNight && PHASE_ROLE[phase] === currentPlayer.role && currentPlayer.alive
  const isAlive = currentPlayer.alive
  // Any OTHER human still alive? If yes, the game advances on their acks
  // alone and the dead caller's buttons do nothing. In pure-spectator
  // mode (all humans dead) the caller's ack matters again — that's
  // the one case where 进入白天/夜晚/观看投票 should stay actionable.
  const aliveHumansExist = gameState.players.some((p) => p.is_human && p.alive)
  const isDeadObserver = !isAlive && aliveHumansExist
  const waitingHumanNames = gameState.players
    .filter((p) => p.is_human && p.alive)
    .map((p) => p.display_name || p.name)
    .join('、')
  const theme = phaseTheme[phase] || { bg: 'bg-card', border: 'border-border' }

  const selectedPlayerName = gameState.players.find((p) => p.id === selectedPlayerId)?.name

  // === Option B helpers ===
  // A night-action POST now returns 200 with `data.pending=true` when the
  // barrier quorum isn't met yet. The intent is stored server-side and the
  // backend auto-replays it when quorum lands (no retry needed here).
  // Returns true when the caller should SKIP the normal "advance phase +
  // success toast" follow-up.
  const handleNightActionResult = (result: ActionResult): boolean => {
    const data = result.data || {}
    if (data.pending === true) {
      // Capture waiting_for so the banner can show names (caller_night_intent
      // alone never carries the list of missing humans).
      const waiting = Array.isArray(data.waiting_for) ? data.waiting_for : []
      setPendingWaitingFor(waiting)
      setMessage('') // suppress generic success toast — the banner speaks for us
      return true
    }
    if (data.already_processed === true) {
      // Idempotent re-click after quorum was met and the intent was
      // already replayed. Silent no-op — polling will reflect the current
      // phase. Don't surface a toast, don't retry.
      return true
    }
    return false
  }

  // Multi-human coordination derived values
  const morningMissing = Math.max(
    0,
    gameState.morning_acks_needed - gameState.morning_acks,
  )
  const isMyTurnToSpeak =
    gameState.current_speaker_id !== null && gameState.current_speaker_id === playerId
  const currentSpeakerName = gameState.current_speaker_id
    ? gameState.players.find((p) => p.id === gameState.current_speaker_id)?.display_name ||
      gameState.players.find((p) => p.id === gameState.current_speaker_id)?.name
    : null

  // === Wolf Discussion (unchanged multi-turn flow) ===
  const handleWolfDiscuss = async () => {
    setLoading(true)
    setMessage('')
    try {
      const updated = await gameApi.wolfDiscuss({
        game_id: gameState.game_id,
        player_id: playerId,
      })
      useGameStore.getState().setGameState(updated)
      setWolfDiscussed(true)
    } catch (error: any) {
      setMessage(`获取队友建议失败: ${error.response?.data?.detail || error.message}`)
    } finally {
      setLoading(false)
    }
  }

  // Send wolf night message → AI reconsiders (multi-turn). Target selection +
  // final "confirm kill" are separate buttons.
  const handleWolfSendMessage = async () => {
    const content = wolfMessage.trim()
    if (!content) return
    setLoading(true)
    setMessage('')
    try {
      const updated = await gameApi.wolfHumanSpeak({
        game_id: gameState.game_id,
        player_id: playerId,
        content,
      })
      useGameStore.getState().setGameState(updated)
      setWolfMessage('')
    } catch (error: any) {
      setMessage(`发送失败: ${error.response?.data?.detail || error.message}`)
    } finally {
      setLoading(false)
    }
  }

  const handleWolfConfirmKill = async () => {
    if (!selectedPlayerId) {
      setMessage('请先选择击杀目标')
      return
    }
    const targetPlayer = gameState.players.find((p) => p.id === selectedPlayerId)
    if (!targetPlayer || !targetPlayer.alive) {
      setMessage('该玩家已出局，请重新选择目标')
      return
    }
    setLoading(true)
    setMessage('')
    try {
      const result = await gameApi.nightAction({
        game_id: gameState.game_id,
        player_id: playerId,
        action_type: 'werewolf_kill',
        target_id: selectedPlayerId,
      })
      // Option B pending-intent path (barrier not met): stash waiting_for
      // names locally and refresh state so caller_night_intent lands. Do
      // NOT drive /advance-night — phase is still NIGHT_WEREWOLF.
      if (handleNightActionResult(result)) {
        const fresh = await gameApi.getGameState(gameState.game_id, playerId)
        useGameStore.getState().setGameState(fresh)
        return
      }
      setMessage(result.message)
      // Human-wolf intent barrier: if we're the first wolf to submit and a
      // teammate human wolf hasn't yet, backend returns `{waiting: true}`
      // and hasn't committed the kill. Fetch latest state to surface the
      // wolf_kill_submitted/total progress, but do NOT drive /advance-night
      // (phase still NIGHT_WEREWOLF, nothing to advance).
      const waiting = result?.data?.waiting === true
      if (waiting) {
        const fresh = await gameApi.getGameState(gameState.game_id, playerId)
        useGameStore.getState().setGameState(fresh)
      } else {
        const finalState = await gameApi.advanceNight({
          game_id: gameState.game_id,
          player_id: playerId,
        })
        useGameStore.getState().setGameState(finalState)
        setWolfDiscussed(false)
      }
    } catch (error: any) {
      setMessage(`操作失败: ${error.response?.data?.detail || error.message}`)
    } finally {
      setLoading(false)
    }
  }

  // === Witch Actions ===
  const loadWitchInfo = async () => {
    setWitchLoading(true)
    try {
      const info = await gameApi.witchInfo(gameState.game_id, playerId)
      setWitchInfo(info)
    } catch (error: any) {
      setMessage(`获取信息失败: ${error.response?.data?.detail || error.message}`)
    } finally {
      setWitchLoading(false)
    }
  }

  const handleWitchAction = async (action: 'save' | 'poison' | 'skip') => {
    setLoading(true)
    setMessage('')
    try {
      const result = await gameApi.nightAction({
        game_id: gameState.game_id,
        player_id: playerId,
        action_type: 'witch_action',
        use_save: action === 'save',
        use_poison: action === 'poison',
        poison_target_id: action === 'poison' ? selectedPlayerId : undefined,
      })
      if (handleNightActionResult(result)) {
        // Pending: keep witchInfo so user can re-submit via same UI;
        // re-fetching state surfaces caller_night_intent.
        const fresh = await gameApi.getGameState(gameState.game_id, playerId)
        useGameStore.getState().setGameState(fresh)
        return
      }
      setMessage(result.message)
      setWitchInfo(null)
      const updated = await gameApi.advanceNight({
        game_id: gameState.game_id,
        player_id: playerId,
      })
      useGameStore.getState().setGameState(updated)
      setWolfDiscussed(false)
    } catch (error: any) {
      setMessage(`操作失败: ${error.response?.data?.detail || error.message}`)
    } finally {
      setLoading(false)
    }
  }

  // === Guard Actions ===
  const loadGuardInfo = async () => {
    setGuardLoading(true)
    try {
      const info = await gameApi.guardInfo(gameState.game_id, playerId)
      setGuardInfo(info)
    } catch (error: any) {
      setMessage(`获取信息失败: ${error.response?.data?.detail || error.message}`)
    } finally {
      setGuardLoading(false)
    }
  }

  const handleGuardAction = async (skip: boolean = false) => {
    if (!skip && !selectedPlayerId) {
      setMessage('请先选择守护目标')
      return
    }
    if (!skip) {
      const targetPlayer = gameState.players.find((p) => p.id === selectedPlayerId)
      if (!targetPlayer || !targetPlayer.alive) {
        setMessage('该玩家已出局，请重新选择目标')
        return
      }
      if (guardInfo?.last_guarded_name && targetPlayer.name === guardInfo.last_guarded_name) {
        setMessage(`不能连续两晚守护同一人（上轮守护了 ${guardInfo.last_guarded_name}）`)
        return
      }
    }
    setLoading(true)
    setMessage('')
    try {
      const result = await gameApi.nightAction({
        game_id: gameState.game_id,
        player_id: playerId,
        action_type: 'guard_action',
        target_id: skip ? undefined : selectedPlayerId,
      })
      if (handleNightActionResult(result)) {
        const fresh = await gameApi.getGameState(gameState.game_id, playerId)
        useGameStore.getState().setGameState(fresh)
        return
      }
      setMessage(result.message)
      const updated = await gameApi.advanceNight({
        game_id: gameState.game_id,
        player_id: playerId,
      })
      useGameStore.getState().setGameState(updated)
      setWolfDiscussed(false)
    } catch (error: any) {
      setMessage(`操作失败: ${error.response?.data?.detail || error.message}`)
    } finally {
      setLoading(false)
    }
  }

  // === Night Actions ===
  const handleNightAction = async (actionType: string) => {
    if (!selectedPlayerId) {
      setMessage('请先选择目标玩家')
      return
    }
    const targetPlayer = gameState.players.find((p) => p.id === selectedPlayerId)
    if (!targetPlayer || !targetPlayer.alive) {
      setMessage('该玩家已出局，请重新选择目标')
      return
    }
    setLoading(true)
    setMessage('')
    try {
      const result = await gameApi.nightAction({
        game_id: gameState.game_id,
        player_id: playerId,
        action_type: actionType,
        target_id: selectedPlayerId,
      })
      if (handleNightActionResult(result)) {
        const fresh = await gameApi.getGameState(gameState.game_id, playerId)
        useGameStore.getState().setGameState(fresh)
        return
      }
      setMessage(result.message)
      const updated = await gameApi.advanceNight({
        game_id: gameState.game_id,
        player_id: playerId,
      })
      useGameStore.getState().setGameState(updated)
      setWolfDiscussed(false)
    } catch (error: any) {
      setMessage(`操作失败: ${error.response?.data?.detail || error.message}`)
    } finally {
      setLoading(false)
    }
  }

  // === Discussion: human speak + advance ===
  const handleSubmitSpeech = async () => {
    setLoading(true)
    setMessage('')
    try {
      const content = speechText.trim() || '（沉默）'
      await gameApi.humanSpeak({
        game_id: gameState.game_id,
        player_id: playerId,
        content,
      })
      setSpeechText('')
      // Kick the next batch of AI speeches / phase transition.
      await advanceDiscussion()
    } catch (error: any) {
      // NOT_YOUR_TURN is the expected multi-human race — someone else's
      // submission already moved the turn. Swallow + re-sync from server.
      const code = error?.response?.data?.detail?.code
      if (code === 'NOT_YOUR_TURN') {
        console.info('[ActionPanel] NOT_YOUR_TURN race; resyncing state')
        try {
          const fresh = await gameApi.getGameState(gameState.game_id, playerId)
          useGameStore.getState().setGameState(fresh)
        } catch {
          /* polling will catch up */
        }
      } else {
        setMessage(`发言失败: ${error.response?.data?.detail?.message || error.message}`)
      }
    } finally {
      setLoading(false)
    }
  }

  // === Dead player: auto AI vote observe ===
  const handleAutoVote = async () => {
    setLoading(true)
    setMessage('')
    try {
      await gameApi.aiVote({
        game_id: gameState.game_id,
        player_id: playerId,
      })
      const updated = await gameApi.getGameState(gameState.game_id, playerId)
      useGameStore.getState().setGameState(updated)
    } catch (error: any) {
      setMessage(`投票失败: ${error.response?.data?.detail || error.message}`)
    } finally {
      setLoading(false)
    }
  }

  // === Hunter Shoot (human hunter) ===
  const handleHunterShoot = async (skip: boolean = false) => {
    if (!skip && !selectedPlayerId) {
      setMessage('请先选择开枪目标')
      return
    }
    if (!skip) {
      const targetPlayer = gameState.players.find((p) => p.id === selectedPlayerId)
      if (!targetPlayer || !targetPlayer.alive) {
        setMessage('该玩家已出局，请重新选择目标')
        return
      }
    }
    setLoading(true)
    setMessage('')
    try {
      const result = await gameApi.hunterShoot({
        game_id: gameState.game_id,
        player_id: playerId,
        action_type: 'hunter_shoot',
        target_id: skip ? undefined : selectedPlayerId,
      })
      setMessage(result.message)
      const updated = await gameApi.getGameState(gameState.game_id, playerId)
      useGameStore.getState().setGameState(updated)
    } catch (error: any) {
      setMessage(`开枪失败: ${error.response?.data?.detail || error.message}`)
    } finally {
      setLoading(false)
    }
  }

  // === Vote Actions (blind barrier) ===
  const handleVote = async (targetId: string | null) => {
    if (targetId) {
      const voteTarget = gameState.players.find((p) => p.id === targetId)
      if (!voteTarget || !voteTarget.alive) {
        setMessage('该玩家已出局，请重新选择投票目标')
        return
      }
    }
    setLoading(true)
    setMessage('')
    try {
      await gameApi.vote({
        game_id: gameState.game_id,
        player_id: playerId,
        target_id: targetId,
      })
      // Whether the backend is still collecting or has resolved, just refresh
      // state. Individual votes never come back through the vote endpoint;
      // the public vote_result event lands in `events` once tally resolves.
      setVoteSubmitted(true)
      const updated = await gameApi.getGameState(gameState.game_id, playerId)
      useGameStore.getState().setGameState(updated)
    } catch (error: any) {
      const code = error?.response?.data?.detail?.code
      if (code === 'NOT_YOUR_SLOT') {
        setMessage('身份不匹配，请刷新页面')
      } else {
        setMessage(`投票失败: ${error.response?.data?.detail?.message || error.message}`)
      }
    } finally {
      setLoading(false)
    }
  }

  // === Render helpers ===
  const TargetHint = () => {
    if (!selectedPlayerId) {
      return (
        <div className="mb-3 flex items-center gap-2 text-sm text-amber-400">
          <Target className="size-4" />
          <span>请在上方选择目标玩家</span>
        </div>
      )
    }
    return (
      <div className="mb-3 flex items-center gap-2 text-sm text-emerald-400">
        <CheckCircle2 className="size-4" />
        <span>
          目标: <strong>{selectedPlayerName}</strong>
        </span>
      </div>
    )
  }

  const renderWitchPanel = () => {
    if (!witchInfo) {
      return (
        <Button
          onClick={loadWitchInfo}
          disabled={witchLoading}
          size="lg"
          className="w-full gap-2 bg-emerald-800 text-white hover:bg-emerald-700"
        >
          <Sparkles className="size-4" />
          {witchLoading ? '加载中...' : '查看今夜情况'}
        </Button>
      )
    }

    return (
      <div className="space-y-3">
        <div className="flex gap-2">
          <div
            className={cn(
              'flex flex-1 items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium',
              witchInfo.save_available
                ? 'border-emerald-700/40 bg-emerald-900/40 text-emerald-300'
                : 'bg-muted/30 border-border/30 text-muted-foreground line-through',
            )}
          >
            <Heart className="size-3.5" />
            解药 {witchInfo.save_available ? '可用' : '已用'}
          </div>
          <div
            className={cn(
              'flex flex-1 items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium',
              witchInfo.poison_available
                ? 'border-purple-700/40 bg-purple-900/40 text-purple-300'
                : 'bg-muted/30 border-border/30 text-muted-foreground line-through',
            )}
          >
            <Skull className="size-3.5" />
            毒药 {witchInfo.poison_available ? '可用' : '已用'}
          </div>
        </div>

        {witchInfo.save_available && (
          <div
            className={cn(
              'rounded-lg border px-3 py-2.5 text-sm',
              witchInfo.victim_name
                ? 'border-red-800/40 bg-red-950/30 text-red-300'
                : 'bg-muted/30 border-border/30 text-muted-foreground',
            )}
          >
            {witchInfo.victim_name ? (
              <>
                今夜 <strong>{witchInfo.victim_name}</strong> 被狼人杀害
              </>
            ) : (
              '今夜是平安夜，无人被杀'
            )}
          </div>
        )}

        <div className="space-y-2">
          {witchInfo.save_available && witchInfo.victim_name && (
            <Button
              onClick={() => handleWitchAction('save')}
              disabled={loading}
              size="lg"
              className="w-full gap-2 bg-emerald-800 text-white hover:bg-emerald-700"
            >
              <Heart className="size-4" />
              {loading ? '处理中...' : `使用解药救 ${witchInfo.victim_name}`}
            </Button>
          )}

          {witchInfo.poison_available && (
            <>
              <TargetHint />
              <Button
                onClick={() => handleWitchAction('poison')}
                disabled={loading || !selectedPlayerId}
                size="lg"
                className="w-full gap-2 bg-purple-800 text-white hover:bg-purple-700"
              >
                <Skull className="size-4" />
                {loading ? '处理中...' : '使用毒药毒杀'}
              </Button>
            </>
          )}

          <Button
            onClick={() => handleWitchAction('skip')}
            disabled={loading}
            variant="outline"
            size="lg"
            className="w-full gap-2"
          >
            <SkipForward className="size-4" />
            {loading ? '处理中...' : '不使用药水'}
          </Button>
        </div>
      </div>
    )
  }

  const renderGuardPanel = () => {
    if (!guardInfo) {
      return (
        <Button
          onClick={loadGuardInfo}
          disabled={guardLoading}
          size="lg"
          className="w-full gap-2 bg-blue-800 text-white hover:bg-blue-700"
        >
          <Shield className="size-4" />
          {guardLoading ? '加载中...' : '查看守护信息'}
        </Button>
      )
    }

    return (
      <div className="space-y-3">
        {guardInfo.last_guarded_name && (
          <div className="rounded-lg border border-blue-800/40 bg-blue-950/30 px-3 py-2.5 text-sm text-blue-300">
            上轮守护了 <strong>{guardInfo.last_guarded_name}</strong>，本轮不能再守护此人
          </div>
        )}

        <TargetHint />
        <div className="flex gap-2">
          <Button
            onClick={() => handleGuardAction(false)}
            disabled={loading || !selectedPlayerId}
            size="lg"
            className="flex-1 gap-2 bg-blue-800 text-white hover:bg-blue-700"
          >
            <Shield className="size-4" />
            {loading
              ? '处理中...'
              : intentMatchesPhase
                ? '覆盖守护目标'
                : '守护此玩家'}
          </Button>
          <Button
            onClick={() => handleGuardAction(true)}
            disabled={loading}
            size="lg"
            variant="outline"
            className="gap-2 border-blue-700/50 text-blue-300 hover:bg-blue-900/30"
          >
            不守护
          </Button>
        </div>
      </div>
    )
  }

  // Enter night with transition animation (shown once per night cycle for all roles).
  // After the animation plays, records the caller's explicit night-ack server-side
  // so `/advance-night` (AI driver) and all night-action endpoints pass their
  // quorum check. Backend failures are swallowed — the auto-advance loop will
  // retry and polling will recover state.
  const handleEnterNight = async () => {
    setLoading(true)
    setMessage('')
    useGameStore.getState().setPhaseTransition('night')
    await new Promise((r) => setTimeout(r, TRANSITION_DURATION))
    try {
      const updated = await gameApi.enterNight({
        game_id: gameState.game_id,
        player_id: playerId,
      })
      useGameStore.getState().setGameState(updated)
    } catch (err) {
      console.error('[ActionPanel] night ack failed:', err)
    }
    setNightEntered(true)
    setLoading(false)
  }

  // === Option B pending-intent rendering ===
  // Show a global banner when the caller has a locked night-action intent
  // still waiting on other humans' /enter-night acks. In single-player,
  // caller_night_intent is never non-null (quorum = 1, first click satisfies
  // it and backend replays immediately → intent cleared) so this is dead
  // code in solo play by design.
  const callerIntent = gameState.caller_night_intent ?? null
  const callerIntentPhaseKind = PHASE_INTENT_KIND[phase]
  const intentMatchesPhase =
    callerIntent !== null && callerIntent.kind === callerIntentPhaseKind

  // Name resolution for the locked target (seer/guard/wolf use target_id;
  // witch uses detail.poison_target_id).
  const resolveTargetName = (id: string | null): string => {
    if (!id) return ''
    return gameState.players.find((p) => p.id === id)?.name || ''
  }

  // Describe the locked intent in Chinese — witch has 3 modes so branch.
  const describeIntent = (): string => {
    if (!callerIntent) return ''
    if (callerIntent.kind === 'witch_action') {
      const d = callerIntent.detail
      if (!d) return '已选：不使用药水'
      const poisonName = resolveTargetName(d.poison_target_id)
      if (d.use_save && d.use_poison)
        return `已选：使用解药 + 毒杀 ${poisonName || '?'}`
      if (d.use_save) return '已选：使用解药'
      if (d.use_poison) return `已选：毒杀 ${poisonName || '?'}`
      return '已选：不使用药水'
    }
    if (callerIntent.target_id === null) {
      // guard 空守 (werewolf_kill + seer_check require a target server-side,
      // so null only happens for guard).
      return '已选：不守护'
    }
    const name = resolveTargetName(callerIntent.target_id)
    return `已选 ${name || '?'}`
  }

  const renderPendingBanner = () => {
    if (!callerIntent) return null
    const needed = gameState.night_acks_needed
    const acked = gameState.night_acks
    const waitingNames =
      pendingWaitingFor.length > 0
        ? pendingWaitingFor
        : // Fallback: derive from alive humans (only meaningful if we have
          // display_name; when loading without a pending POST response, the
          // banner falls back to a count-only string).
          []
    const waitingText =
      waitingNames.length > 0
        ? waitingNames.join('、')
        : `其他玩家`
    return (
      <div className="animate-fade-in mb-3 rounded-lg border border-indigo-800/50 bg-indigo-950/40 px-3 py-2.5 text-sm text-indigo-200">
        <div className="flex items-center gap-2">
          <Hourglass className="size-4 shrink-0 animate-pulse text-indigo-300" />
          <span>
            你的选择已锁定 · 等待 <strong>{waitingText}</strong> 进入夜晚 ({acked}/{needed})
          </span>
        </div>
        <div className="text-muted-foreground/80 mt-1 ml-6 text-xs">
          {describeIntent()} · 点击其他目标可覆盖选择
        </div>
      </div>
    )
  }

  const renderNightActions = () => {
    if (!nightEntered) {
      // Dead observer: alive humans drive the night. Our click would
      // record an ack that's filtered out of the quorum anyway — show
      // a passive waiting state instead of a button that does nothing.
      if (isDeadObserver) {
        return (
          <div className="flex items-center justify-center gap-2 rounded-lg border border-indigo-800/40 bg-indigo-950/30 px-3 py-4 text-sm text-indigo-200">
            <span className="size-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
            <span>
              观战中：等待 <strong>{waitingHumanNames}</strong> 进入夜晚
            </span>
          </div>
        )
      }
      return (
        <Button
          onClick={handleEnterNight}
          disabled={loading}
          size="lg"
          className="w-full gap-2 bg-indigo-900 text-white hover:bg-indigo-800"
        >
          <Moon className="size-4" />
          {loading ? '夜晚进行中...' : '进入夜晚'}
        </Button>
      )
    }

    if (isMyNightTurn) {
      return (
        <div className="space-y-3">
          {phase === 'NIGHT_WEREWOLF' && !wolfDiscussed && (
            <Button
              onClick={handleWolfDiscuss}
              disabled={loading}
              size="lg"
              className="w-full gap-2 bg-red-900 text-white hover:bg-red-800"
            >
              <MessageSquare className="size-4" />
              {loading ? (
                <>
                  <span className="size-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                  队友思考中...
                </>
              ) : (
                '开始狼人讨论'
              )}
            </Button>
          )}
          {phase === 'NIGHT_WEREWOLF' && wolfDiscussed && (
            <div className="space-y-3">
              <div>
                <label className="mb-2 block text-sm text-red-300/80">
                  <MessageSquare className="mr-1.5 inline size-3.5" />
                  和队友聊（AI 会回应并可能改变想法，你可以继续发消息直到确认击杀）
                </label>
                <div className="flex gap-2">
                  <Textarea
                    value={wolfMessage}
                    onChange={(e) => setWolfMessage(e.target.value)}
                    placeholder="例如：你明天跳预言家，我配合投票..."
                    disabled={loading}
                    className="resize-none border-red-800/30 bg-red-950/30 text-red-100 placeholder:text-red-300/30 focus-visible:ring-red-500/50"
                    rows={2}
                  />
                  <Button
                    onClick={handleWolfSendMessage}
                    disabled={loading || !wolfMessage.trim()}
                    size="sm"
                    className="self-start bg-red-800 hover:bg-red-700"
                  >
                    发送
                  </Button>
                </div>
              </div>

              {/* Two distinct barriers can gate a human wolf's kill:
                  1. Option B enter-night barrier: some alive human hasn't
                     ack'd 进入夜晚 yet. caller_night_intent is non-null;
                     the global banner above explains the wait. Here we
                     still let the wolf overwrite their target.
                  2. Wolf-intent barrier: every human has ack'd 进入夜晚
                     but another human wolf teammate hasn't locked in yet.
                     caller_night_intent is null (replay already happened
                     on this caller's side) but wolf_kill_submitted <
                     wolf_kill_total. */}
              {callerIntent === null &&
              gameState.caller_wolf_kill_submitted &&
              gameState.wolf_kill_submitted < gameState.wolf_kill_total ? (
                <Button
                  disabled
                  variant="destructive"
                  size="lg"
                  className="w-full gap-2 opacity-80"
                >
                  <Hourglass className="size-4 animate-pulse" />
                  已锁定目标，等待其他狼人 ({gameState.wolf_kill_submitted}/
                  {gameState.wolf_kill_total})
                </Button>
              ) : (
                <>
                  <TargetHint />
                  <Button
                    onClick={handleWolfConfirmKill}
                    disabled={loading || !selectedPlayerId}
                    variant="destructive"
                    size="lg"
                    className="w-full gap-2"
                  >
                    <Crosshair className="size-4" />
                    {loading ? (
                      <>
                        <span className="size-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                        处理中...
                      </>
                    ) : intentMatchesPhase ? (
                      '覆盖击杀目标'
                    ) : (
                      '确认击杀目标'
                    )}
                  </Button>
                </>
              )}
            </div>
          )}
          {phase === 'NIGHT_SEER' && (
            <>
              <TargetHint />
              <Button
                onClick={() => handleNightAction('seer_check')}
                disabled={loading || !selectedPlayerId}
                size="lg"
                className="w-full gap-2 bg-purple-800 text-white hover:bg-purple-700"
              >
                <Eye className="size-4" />
                {loading
                  ? '处理中...'
                  : intentMatchesPhase
                    ? '覆盖查验目标'
                    : '查验此玩家'}
              </Button>
            </>
          )}
          {phase === 'NIGHT_WITCH' && renderWitchPanel()}
          {phase === 'NIGHT_GUARD' && renderGuardPanel()}
        </div>
      )
    }

    return (
      <Button disabled size="lg" className="w-full gap-2 bg-indigo-900 text-white">
        <span className="size-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
        夜晚进行中...
      </Button>
    )
  }

  // === Discussion rendering ===
  const renderDiscussion = () => {
    // Morning-ack barrier: we entered day but others haven't.
    if (morningMissing > 0) {
      return (
        <div className="flex items-center justify-center gap-2 rounded-lg border border-amber-800/40 bg-amber-950/30 px-3 py-4 text-sm text-amber-300">
          <Hourglass className="size-4 animate-pulse" />
          等待 {morningMissing} 位玩家进入白天...
        </div>
      )
    }

    // AI speaking or log still animating — show waiting unless it's my turn
    if (!isMyTurnToSpeak && (gameState.ai_speaking || !allEventsRevealed)) {
      return (
        <Button disabled size="lg" className="w-full gap-2 bg-amber-800 text-white">
          <span className="size-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
          {currentSpeakerName && gameState.current_speaker_id
            ? `等待 ${currentSpeakerName} 发言...`
            : 'AI 发言中...'}
        </Button>
      )
    }

    // Another human is the current speaker
    if (gameState.current_speaker_id && !isMyTurnToSpeak) {
      return (
        <div className="flex items-center justify-center gap-2 rounded-lg border border-amber-800/40 bg-amber-950/30 px-3 py-4 text-sm text-amber-300">
          <Hourglass className="size-4 animate-pulse" />
          等待 <strong>{currentSpeakerName}</strong> 发言...
        </div>
      )
    }

    // My turn to speak
    if (isMyTurnToSpeak) {
      return (
        <div className="space-y-4">
          <div>
            <label className="text-muted-foreground mb-2 block text-sm">
              <MessageSquare className="mr-1.5 inline size-3.5" />
              你的发言（身份: {ROLE_LABEL[currentPlayer.role] || '未知'}）
            </label>
            <Textarea
              value={speechText}
              onChange={(e) => setSpeechText(e.target.value)}
              placeholder="输入你的发言..."
              disabled={loading}
              className="bg-muted/50 border-border/50 placeholder:text-muted-foreground/50 resize-none focus-visible:ring-amber-500/50"
              rows={3}
            />
          </div>
          <Button
            onClick={handleSubmitSpeech}
            disabled={loading}
            size="lg"
            className="w-full gap-2 bg-emerald-800 text-white hover:bg-emerald-700"
          >
            {loading ? (
              <>
                <span className="size-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                提交中...
              </>
            ) : (
              '提交发言并继续'
            )}
          </Button>
        </div>
      )
    }

    // No current speaker but phase still DAY_DISCUSSION — transitional state
    return (
      <Button disabled size="lg" className="w-full gap-2 bg-amber-800 text-white">
        <span className="size-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
        讨论进行中...
      </Button>
    )
  }

  const renderVoteBlock = () => {
    if (!isAlive) {
      // Dead observer: alive humans drive the vote. Clicking 观看投票
      // before they've all voted is a no-op — so render a passive
      // waiting row instead of a pretend button. Only in pure-spectator
      // mode (no alive humans) does the caller's click actually drive
      // vote resolution, and THAT is where the button stays live.
      if (isDeadObserver) {
        return (
          <div className="flex items-center justify-center gap-2 rounded-lg border border-orange-800/40 bg-orange-950/30 px-3 py-4 text-sm text-orange-200">
            <span className="size-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
            <span>
              观战中：等待 <strong>{waitingHumanNames}</strong> 投票 (
              {gameState.vote_submitted}/{gameState.vote_total})
            </span>
          </div>
        )
      }
      // Pure-spectator mode (all humans dead). If this caller has
      // already ack'd "观看投票" but the other spectator hasn't, show a
      // locked-in waiting state — previously the button stayed clickable
      // and the click looked like a no-op.
      if (gameState.caller_vote_watch_acked) {
        const submitted = gameState.vote_watch_submitted ?? 0
        const total = gameState.vote_watch_total ?? 0
        return (
          <div className="flex items-center justify-center gap-2 rounded-lg border border-orange-800/40 bg-orange-950/30 px-3 py-4 text-sm text-orange-200">
            <span className="size-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
            <span>
              已观看，等待其他出局玩家 ({submitted}/{total})
            </span>
          </div>
        )
      }
      return (
        <Button
          onClick={handleAutoVote}
          disabled={loading || !allEventsRevealed}
          size="lg"
          className="w-full gap-2 bg-orange-800 text-white hover:bg-orange-700"
        >
          {loading ? (
            <>
              <span className="size-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
              投票中...
            </>
          ) : !allEventsRevealed ? (
            <>
              <span className="size-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
              等待发言结束...
            </>
          ) : (
            <>
              <Vote className="size-4" />
              观看投票
            </>
          )}
        </Button>
      )
    }

    const progressLine = (
      <div className="text-muted-foreground flex items-center justify-center gap-2 text-xs">
        <span>
          已投票 {gameState.vote_submitted}/{gameState.vote_total}
        </span>
      </div>
    )

    if (voteSubmitted) {
      return (
        <div className="space-y-3 rounded-lg border border-orange-800/40 bg-orange-950/30 p-4 text-center text-sm">
          <div className="flex items-center justify-center gap-2 text-orange-300">
            <CheckCircle2 className="size-4" />
            已投票，等待其他玩家
          </div>
          {progressLine}
        </div>
      )
    }

    return (
      <div className="space-y-2">
        <TargetHint />
        <Button
          onClick={() => handleVote(selectedPlayerId ?? null)}
          disabled={loading || !selectedPlayerId || !allEventsRevealed}
          size="lg"
          className="w-full gap-2 bg-orange-800 text-white hover:bg-orange-700"
        >
          {!allEventsRevealed ? (
            <>
              <span className="size-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
              等待发言结束...
            </>
          ) : (
            <>
              <Vote className="size-4" />
              投票处决
            </>
          )}
        </Button>
        <Button
          onClick={() => handleVote(null)}
          disabled={loading || !allEventsRevealed}
          variant="outline"
          size="lg"
          className="text-muted-foreground w-full gap-2"
        >
          <Ban className="size-4" />
          弃权
        </Button>
        {progressLine}
      </div>
    )
  }

  return (
    <Card className={cn('border', theme.border, theme.bg, 'backdrop-blur-sm')}>
      <CardHeader className="pb-3">
        <CardTitle className="font-display flex items-center gap-2 text-lg tracking-wide">
          {isNight && <Moon className="size-5 text-indigo-400" />}
          {phase === 'DAY_DISCUSSION' && <MessageSquare className="size-5 text-amber-400" />}
          {phase === 'DAY_VOTE' && <Vote className="size-5 text-orange-400" />}
          {phase === 'HUNTER_SHOOT' && <Crosshair className="size-5 text-orange-400" />}
          操作面板
        </CardTitle>
      </CardHeader>

      <Separator className="bg-border/30" />

      <CardContent className="pt-4">
        {/* Option B: global night-intent pending banner. Shows only when the
            caller has a locked intent still waiting on other humans' night
            acks — in single-player this is never rendered (quorum=1, intent
            is replayed immediately so caller_night_intent is always null). */}
        {isNight && renderPendingBanner()}

        {/* Dead player hint */}
        {!isAlive && dayEntered && (phase === 'DAY_DISCUSSION' || phase === 'DAY_VOTE') && (
          <div className="text-muted-foreground mb-3 flex items-center gap-2 text-sm">
            <AlertCircle className="size-4" />
            <span>你已出局，作为旁观者继续观战</span>
          </div>
        )}

        <div className="mb-3">
          {isNight && renderNightActions()}
          {phase.startsWith('DAY_') && !dayEntered && (
            isDeadObserver ? (
              // Dead observer: alive humans' 进入白天 drives the morning.
              // Passive waiting row, no clickable button.
              <div className="flex items-center justify-center gap-2 rounded-lg border border-amber-800/40 bg-amber-950/30 px-3 py-4 text-sm text-amber-200">
                <span className="size-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                <span>
                  观战中：等待 <strong>{waitingHumanNames}</strong> 进入白天
                </span>
              </div>
            ) : (
              <Button
                onClick={async () => {
                  setLoading(true)
                  useGameStore.getState().setPhaseTransition('day')
                  await new Promise((r) => setTimeout(r, TRANSITION_DURATION))
                  setDayEntered(true)
                  // Record morning ack explicitly. `/enter-day` records ONLY
                  // the caller's identity, which is what this button represents.
                  // (Historically this called `/advance-night`, which silently
                  // filled all humans' morning_acks via the auto-advance loop
                  // and broke multi-human barriers.)
                  try {
                    const updated = await gameApi.enterDay({
                      game_id: gameState.game_id,
                      player_id: playerId,
                    })
                    useGameStore.getState().setGameState(updated)
                  } catch (err) {
                    console.error('[ActionPanel] morning ack failed:', err)
                  }
                  setLoading(false)
                }}
                disabled={loading}
                size="lg"
                className="w-full gap-2 bg-amber-800 text-white hover:bg-amber-700"
              >
                <Sun className="size-4" />
                {loading ? '天亮了...' : '进入白天'}
              </Button>
            )
          )}
          {phase === 'DAY_DISCUSSION' && dayEntered && isAlive && renderDiscussion()}
          {phase === 'DAY_DISCUSSION' && dayEntered && !isAlive && (
            <div className="flex items-center justify-center gap-2 rounded-lg border border-amber-800/40 bg-amber-950/30 px-3 py-4 text-sm text-amber-300">
              {gameState.ai_speaking || !allEventsRevealed ? (
                <>
                  <span className="size-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                  观战中：AI 发言中...
                </>
              ) : (
                <>
                  <Hourglass className="size-4 animate-pulse" />
                  观战中，等待讨论结束...
                </>
              )}
            </div>
          )}
          {phase === 'DAY_VOTE' && dayEntered && renderVoteBlock()}
          {phase === 'HUNTER_SHOOT' && currentPlayer.role === 'hunter' && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-sm text-orange-300">
                <Crosshair className="size-4" />
                <span>你被淘汰了！作为猎人，你可以开枪带走一名玩家，也可以选择不开枪</span>
              </div>
              <TargetHint />
              <div className="flex gap-2">
                <Button
                  onClick={() => handleHunterShoot(false)}
                  disabled={loading || !selectedPlayerId}
                  variant="destructive"
                  size="lg"
                  className="flex-1 gap-2"
                >
                  <Crosshair className="size-4" />
                  {loading ? '开枪中...' : '开枪带走'}
                </Button>
                <Button
                  onClick={() => handleHunterShoot(true)}
                  disabled={loading}
                  size="lg"
                  variant="outline"
                  className="gap-2 border-orange-700/50 text-orange-300 hover:bg-orange-900/30"
                >
                  不开枪
                </Button>
              </div>
            </div>
          )}
          {phase === 'HUNTER_SHOOT' && currentPlayer.role !== 'hunter' && (
            <div className="text-muted-foreground flex items-center justify-center gap-2 py-4 text-sm">
              <span className="border-muted-foreground/30 border-t-muted-foreground size-4 animate-spin rounded-full border-2" />
              猎人正在选择目标...
            </div>
          )}
        </div>

        {message && (
          <div
            className={cn(
              'animate-fade-in flex items-start gap-2 rounded-lg p-3 text-sm',
              message.includes('失败')
                ? 'bg-destructive/10 border-destructive/30 border text-red-300'
                : 'border border-emerald-800/30 bg-emerald-900/20 text-emerald-300',
            )}
          >
            {message.includes('失败') ? (
              <AlertCircle className="mt-0.5 size-4 shrink-0" />
            ) : (
              <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
            )}
            <span>{message}</span>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
