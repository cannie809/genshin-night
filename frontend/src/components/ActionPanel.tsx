import { useState, useEffect, useRef } from 'react';
import { Crosshair, Eye, Sparkles, MessageSquare, Vote, Moon, Sun, Target, AlertCircle, CheckCircle2, Heart, Skull, SkipForward, Ban, Shield } from 'lucide-react';
import { useGameStore } from '../store/gameStore';
import { gameApi } from '../api/client';
import { TRANSITION_DURATION } from './PhaseTransition';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Separator } from '@/components/ui/separator';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { cn } from '@/lib/utils';

interface ActionPanelProps {
  selectedPlayerId?: string;
}

// Map night phase to the role that acts
const PHASE_ROLE: Record<string, string> = {
  NIGHT_GUARD: 'guard',
  NIGHT_WEREWOLF: 'werewolf',
  NIGHT_SEER: 'seer',
  NIGHT_WITCH: 'witch',
};

const ROLE_LABEL: Record<string, string> = {
  werewolf: '狼人',
  seer: '预言家',
  witch: '女巫',
  hunter: '猎人',
  guard: '守卫',
  villager: '村民',
  '???': '未知',
};

const phaseTheme: Record<string, { bg: string; border: string }> = {
  NIGHT_GUARD: { bg: 'bg-blue-950/30', border: 'border-blue-900/30' },
  NIGHT_WEREWOLF: { bg: 'bg-red-950/30', border: 'border-red-900/30' },
  NIGHT_SEER: { bg: 'bg-purple-950/30', border: 'border-purple-900/30' },
  NIGHT_WITCH: { bg: 'bg-emerald-950/30', border: 'border-emerald-900/30' },
  DAY_DISCUSSION: { bg: 'bg-amber-950/20', border: 'border-amber-900/30' },
  DAY_VOTE: { bg: 'bg-orange-950/20', border: 'border-orange-900/30' },
  HUNTER_SHOOT: { bg: 'bg-orange-950/30', border: 'border-orange-900/30' },
};

// Discussion sub-phases
type DiscussionStep = 'init' | 'awaiting_human' | 'done';

export function ActionPanel({ selectedPlayerId }: ActionPanelProps) {
  const { gameState, playerId, allEventsRevealed } = useGameStore();
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [speechText, setSpeechText] = useState('');
  const [discussionStep, setDiscussionStep] = useState<DiscussionStep>('init');
  const [waitingForReveal, setWaitingForReveal] = useState(false);
  const [nightEntered, setNightEntered] = useState(false);
  const [dayEntered, setDayEntered] = useState(false);

  // When waiting for reveal and all events are shown (and AI done speaking), transition to human input.
  // Uses zustand subscribe to avoid stale closure race condition.
  useEffect(() => {
    if (!waitingForReveal) return;

    const transition = () => {
      setWaitingForReveal(false);
      setDiscussionStep('awaiting_human');
      setMessage('轮到你发言了');
      setLoading(false);
    };

    const isReady = (s: ReturnType<typeof useGameStore.getState>) =>
      s.allEventsRevealed && !s.gameState?.ai_speaking;

    // Delayed initial check — fallback in case subscribe misses the transition
    const timer = setTimeout(() => {
      if (isReady(useGameStore.getState())) {
        transition();
      }
    }, 500);

    // Subscribe for future changes (when stagger animation completes + ai_speaking turns off)
    const unsub = useGameStore.subscribe((state, prevState) => {
      if (isReady(state) && !isReady(prevState)) {
        transition();
      }
    });

    return () => {
      clearTimeout(timer);
      unsub();
    };
  }, [waitingForReveal]);

  // Auto-advance through night phases that aren't the player's turn
  // (e.g., player is werewolf → skip witch/seer phases automatically)
  const nightAutoAdvRef = useRef(false);
  useEffect(() => {
    const phase = gameState?.phase ?? '';
    const currentPlayer = gameState?.players.find((p) => p.id === playerId);
    const isNight = phase.startsWith('NIGHT_');
    const isMyNightTurn = isNight && PHASE_ROLE[phase] === currentPlayer?.role && !!currentPlayer?.alive;

    if (!nightEntered || !isNight || isMyNightTurn || !gameState || !playerId) {
      nightAutoAdvRef.current = false;
      return;
    }
    if (nightAutoAdvRef.current) return;
    nightAutoAdvRef.current = true;

    (async () => {
      try {
        setLoading(true);
        const updated = await gameApi.advanceNight({
          game_id: gameState.game_id,
          player_id: playerId,
        });
        useGameStore.getState().setGameState(updated);
        setDiscussionStep('init');
        setWolfDiscussed(false);
      } catch (error: any) {
        setMessage(`操作失败: ${error.response?.data?.detail || error.message}`);
      } finally {
        nightAutoAdvRef.current = false;
        setLoading(false);
      }
    })();
  }, [nightEntered, gameState?.phase, playerId]);

  // Auto-trigger AI hunter shoot when non-hunter player enters HUNTER_SHOOT phase.
  // Uses ref instead of state to prevent double-trigger from React re-renders.
  const hunterShootRef = useRef(false);
  useEffect(() => {
    if (!gameState || !playerId) return;
    if (gameState.phase !== 'HUNTER_SHOOT') {
      hunterShootRef.current = false;
      return;
    }
    const player = gameState.players.find(p => p.id === playerId);
    if (!player || player.role === 'hunter' || hunterShootRef.current) return;

    hunterShootRef.current = true;
    const hunterEvent = gameState.events.find(
      (e: any) => e.type === 'hunter_death_vote' || e.type === 'hunter_death_night'
    );
    const hunterId = hunterEvent?.data?.hunter_id;
    if (hunterId) {
      gameApi.hunterShoot({
        game_id: gameState.game_id,
        player_id: hunterId,
        action_type: 'hunter_shoot',
      }).then(async () => {
        const updated = await gameApi.getGameState(gameState.game_id, playerId);
        useGameStore.getState().setGameState(updated);
      }).catch(() => {
        // Retry via polling — game state refresh will pick up the result
      });
    }
  }, [gameState?.phase, playerId]);

  // Reset transition flags when switching between day/night
  useEffect(() => {
    if (gameState?.phase.startsWith('DAY_')) {
      setNightEntered(false);
    } else if (gameState?.phase.startsWith('NIGHT_')) {
      setDayEntered(false);
    }
    // Reset guard info when leaving guard phase
    if (gameState?.phase !== 'NIGHT_GUARD') {
      setGuardInfo(null);
    }
  }, [gameState?.phase]);

  const [voteResult, setVoteResult] = useState<{
    vote_summary: Record<string, { votes: number; voters: string[] }>;
    eliminated?: string;
    tie?: boolean;
  } | null>(null);
  const [witchInfo, setWitchInfo] = useState<{
    victim_name: string | null;
    save_available: boolean;
    poison_available: boolean;
  } | null>(null);
  const [witchLoading, setWitchLoading] = useState(false);
  const [guardInfo, setGuardInfo] = useState<{
    last_guarded_name: string | null;
  } | null>(null);
  const [guardLoading, setGuardLoading] = useState(false);
  const [wolfDiscussed, setWolfDiscussed] = useState(false);
  const [wolfMessage, setWolfMessage] = useState('');

  if (!gameState) return null;

  const currentPlayer = gameState.players.find((p) => p.id === playerId);
  if (!currentPlayer) return null;

  const phase = gameState.phase;
  const isNight = phase.startsWith('NIGHT_');
  const isMyNightTurn = isNight && PHASE_ROLE[phase] === currentPlayer.role && currentPlayer.alive;
  const isAlive = currentPlayer.alive;
  const theme = phaseTheme[phase] || { bg: 'bg-card', border: 'border-border' };

  const selectedPlayerName = gameState.players.find(p => p.id === selectedPlayerId)?.name;

  // === Wolf Discussion ===
  const handleWolfDiscuss = async () => {
    setLoading(true);
    setMessage('');
    try {
      const updated = await gameApi.wolfDiscuss({
        game_id: gameState.game_id,
        player_id: playerId,
      });
      useGameStore.getState().setGameState(updated);
      setWolfDiscussed(true);
    } catch (error: any) {
      setMessage(`获取队友建议失败: ${error.response?.data?.detail || error.message}`);
    } finally {
      setLoading(false);
    }
  };

  // Submit wolf night message + kill target together
  const handleWolfSubmitAndKill = async () => {
    if (!selectedPlayerId) {
      setMessage('请先选择击杀目标');
      return;
    }
    // Guard: verify target is still alive (prevents stale selection of dead player)
    const targetPlayer = gameState.players.find(p => p.id === selectedPlayerId);
    if (!targetPlayer || !targetPlayer.alive) {
      setMessage('该玩家已出局，请重新选择目标');
      return;
    }
    setLoading(true);
    setMessage('');
    try {
      // Step 1: Submit human's night message if any
      const content = wolfMessage.trim();
      if (content) {
        const updated = await gameApi.wolfHumanSpeak({
          game_id: gameState.game_id,
          player_id: playerId,
          content,
        });
        useGameStore.getState().setGameState(updated);
      }
      setWolfMessage('');

      // Step 2: Submit kill target
      const result = await gameApi.nightAction({
        game_id: gameState.game_id,
        player_id: playerId,
        action_type: 'werewolf_kill',
        target_id: selectedPlayerId,
      });
      setMessage(result.message);

      // Step 3: Advance night
      const finalState = await gameApi.advanceNight({
        game_id: gameState.game_id,
        player_id: playerId,
      });
      useGameStore.getState().setGameState(finalState);
      setDiscussionStep('init');
      setWolfDiscussed(false);
    } catch (error: any) {
      setMessage(`操作失败: ${error.response?.data?.detail || error.message}`);
    } finally {
      setLoading(false);
    }
  };

  // === Witch Actions ===
  const loadWitchInfo = async () => {
    setWitchLoading(true);
    try {
      const info = await gameApi.witchInfo(gameState.game_id, playerId);
      setWitchInfo(info);
    } catch (error: any) {
      setMessage(`获取信息失败: ${error.response?.data?.detail || error.message}`);
    } finally {
      setWitchLoading(false);
    }
  };

  const handleWitchAction = async (action: 'save' | 'poison' | 'skip') => {
    setLoading(true);
    setMessage('');
    try {
      const result = await gameApi.nightAction({
        game_id: gameState.game_id,
        player_id: playerId,
        action_type: 'witch_action',
        use_save: action === 'save',
        use_poison: action === 'poison',
        poison_target_id: action === 'poison' ? selectedPlayerId : undefined,
      });
      setMessage(result.message);
      setWitchInfo(null);
      const updated = await gameApi.advanceNight({
        game_id: gameState.game_id,
        player_id: playerId,
      });
      useGameStore.getState().setGameState(updated);
      setDiscussionStep('init');
      setWolfDiscussed(false);
    } catch (error: any) {
      setMessage(`操作失败: ${error.response?.data?.detail || error.message}`);
    } finally {
      setLoading(false);
    }
  };

  // === Guard Actions ===
  const loadGuardInfo = async () => {
    setGuardLoading(true);
    try {
      const info = await gameApi.guardInfo(gameState.game_id, playerId);
      setGuardInfo(info);
    } catch (error: any) {
      setMessage(`获取信息失败: ${error.response?.data?.detail || error.message}`);
    } finally {
      setGuardLoading(false);
    }
  };

  const handleGuardAction = async (skip: boolean = false) => {
    if (!skip && !selectedPlayerId) {
      setMessage('请先选择守护目标');
      return;
    }
    if (!skip) {
      // Check consecutive guard restriction
      const targetPlayer = gameState.players.find(p => p.id === selectedPlayerId);
      if (!targetPlayer || !targetPlayer.alive) {
        setMessage('该玩家已出局，请重新选择目标');
        return;
      }
      if (guardInfo?.last_guarded_name && targetPlayer.name === guardInfo.last_guarded_name) {
        setMessage(`不能连续两晚守护同一人（上轮守护了 ${guardInfo.last_guarded_name}）`);
        return;
      }
    }
    setLoading(true);
    setMessage('');
    try {
      const result = await gameApi.nightAction({
        game_id: gameState.game_id,
        player_id: playerId,
        action_type: 'guard_action',
        target_id: skip ? undefined : selectedPlayerId,
      });
      setMessage(result.message);
      const updated = await gameApi.advanceNight({
        game_id: gameState.game_id,
        player_id: playerId,
      });
      useGameStore.getState().setGameState(updated);
      setDiscussionStep('init');
      setWolfDiscussed(false);
    } catch (error: any) {
      setMessage(`操作失败: ${error.response?.data?.detail || error.message}`);
    } finally {
      setLoading(false);
    }
  };

  // === Night Actions ===
  const handleNightAction = async (actionType: string) => {
    if (!selectedPlayerId) {
      setMessage('请先选择目标玩家');
      return;
    }
    // Guard: verify target is still alive
    const targetPlayer = gameState.players.find(p => p.id === selectedPlayerId);
    if (!targetPlayer || !targetPlayer.alive) {
      setMessage('该玩家已出局，请重新选择目标');
      return;
    }
    setLoading(true);
    setMessage('');
    try {
      const result = await gameApi.nightAction({
        game_id: gameState.game_id,
        player_id: playerId,
        action_type: actionType,
        target_id: selectedPlayerId,
      });
      setMessage(result.message);
      const updated = await gameApi.advanceNight({
        game_id: gameState.game_id,
        player_id: playerId,
      });
      useGameStore.getState().setGameState(updated);
      setDiscussionStep('init');
      setWolfDiscussed(false);
    } catch (error: any) {
      setMessage(`操作失败: ${error.response?.data?.detail || error.message}`);
    } finally {
      setLoading(false);
    }
  };

  // === Discussion Actions (two-phase) ===

  // Step 1: Generate pre-human speeches
  const handlePreDiscussion = async () => {
    setLoading(true);
    setMessage('');
    try {
      const oldEventCount = gameState.events.length;
      const updated = await gameApi.preDiscussion({
        game_id: gameState.game_id,
        player_id: playerId,
      });
      const hasNewEvents = updated.events.length > oldEventCount;

      // If no new events and AI not speaking (human is first), skip reveal wait
      if (!hasNewEvents && !updated.ai_speaking) {
        useGameStore.getState().setGameState(updated);
        setDiscussionStep('awaiting_human');
        setMessage('轮到你发言了');
        setLoading(false);
        return;
      }

      // Reset revealed flag BEFORE setting game state, so the waitingForReveal
      // effect won't see a stale allEventsRevealed=true from the previous state
      useGameStore.getState().setAllEventsRevealed(false);
      useGameStore.getState().setGameState(updated);
      // Don't show human input immediately — wait for all events to be revealed
      setWaitingForReveal(true);
      // Note: loading stays true until the useEffect fires when allEventsRevealed becomes true
    } catch (error: any) {
      setMessage(`讨论失败: ${error.response?.data?.detail || error.message}`);
      setLoading(false);
    }
  };

  // Step 2: Human submits speech, then launch background task for remaining speeches
  const handleSubmitAndFinish = async () => {
    setLoading(true);
    setMessage('');
    try {
      const content = speechText.trim() || '（沉默）';

      // Submit human speech
      await gameApi.humanSpeak({
        game_id: gameState.game_id,
        player_id: playerId,
        content,
      });

      // Immediately show human speech in game log (don't wait for AI)
      const humanPlayer = gameState.players.find(p => p.id === playerId);
      useGameStore.getState().setGameState({
        ...gameState,
        events: [
          ...gameState.events,
          {
            type: 'speech',
            round: gameState.round_number,
            phase: 'DAY_DISCUSSION',
            message: `【${humanPlayer?.name || '旅行者'}】: ${content}`,
          },
        ],
      });
      setSpeechText('');

      // Launch background task for remaining speeches (returns immediately)
      const updated = await gameApi.finishDiscussion({
        game_id: gameState.game_id,
        player_id: playerId,
      });
      useGameStore.getState().setGameState(updated);
      setDiscussionStep('init');
      // Loading off — polling will pick up new speeches and phase transition
      setLoading(false);
    } catch (error: any) {
      setMessage(`讨论失败: ${error.response?.data?.detail || error.message}`);
      setLoading(false);
    }
  };

  // === Dead player: run full discussion without human input ===
  const handleAutoDiscussion = async () => {
    setLoading(true);
    setMessage('');
    try {
      // Launch pre-discussion background task (returns immediately)
      const preState = await gameApi.preDiscussion({
        game_id: gameState.game_id,
        player_id: playerId,
      });
      useGameStore.getState().setGameState(preState);

      // Wait for pre-discussion background task to complete before calling finish
      const waitForAiDone = async () => {
        for (let i = 0; i < 60; i++) { // max ~90s
          await new Promise(r => setTimeout(r, 1500));
          const state = await gameApi.getGameState(gameState.game_id, playerId);
          useGameStore.getState().setGameState(state);
          if (!state.ai_speaking) return;
        }
      };
      if (preState.ai_speaking) {
        await waitForAiDone();
      }

      // Launch finish-discussion background task (returns immediately)
      const updated = await gameApi.finishDiscussion({
        game_id: gameState.game_id,
        player_id: playerId,
      });
      useGameStore.getState().setGameState(updated);
      // Polling will handle the rest (phase transition to DAY_VOTE)
    } catch (error: any) {
      setMessage(`讨论失败: ${error.response?.data?.detail || error.message}`);
    } finally {
      setLoading(false);
    }
  };

  // === Dead player: auto AI vote ===
  const handleAutoVote = async () => {
    setLoading(true);
    setMessage('');
    try {
      const result = await gameApi.aiVote({
        game_id: gameState.game_id,
        player_id: playerId,
      });
      if (result.data?.vote_summary) {
        setVoteResult({
          vote_summary: result.data.vote_summary,
          eliminated: result.data.eliminated,
          tie: result.data.tie,
        });
      }
      const updated = await gameApi.getGameState(gameState.game_id, playerId);
      useGameStore.getState().setGameState(updated);
    } catch (error: any) {
      setMessage(`投票失败: ${error.response?.data?.detail || error.message}`);
    } finally {
      setLoading(false);
    }
  };

  // === Hunter Shoot (human hunter) ===
  const handleHunterShoot = async (skip: boolean = false) => {
    if (!skip && !selectedPlayerId) {
      setMessage('请先选择开枪目标');
      return;
    }
    if (!skip) {
      const targetPlayer = gameState.players.find(p => p.id === selectedPlayerId);
      if (!targetPlayer || !targetPlayer.alive) {
        setMessage('该玩家已出局，请重新选择目标');
        return;
      }
    }
    setLoading(true);
    setMessage('');
    try {
      const result = await gameApi.hunterShoot({
        game_id: gameState.game_id,
        player_id: playerId,
        action_type: 'hunter_shoot',
        target_id: skip ? undefined : selectedPlayerId,
      });
      setMessage(result.message);
      const updated = await gameApi.getGameState(gameState.game_id, playerId);
      useGameStore.getState().setGameState(updated);
    } catch (error: any) {
      setMessage(`开枪失败: ${error.response?.data?.detail || error.message}`);
    } finally {
      setLoading(false);
    }
  };

  // === Vote Actions ===
  const handleVote = async () => {
    if (!selectedPlayerId) {
      setMessage('请先选择投票目标');
      return;
    }
    // Guard: verify target is still alive
    const voteTarget = gameState.players.find(p => p.id === selectedPlayerId);
    if (!voteTarget || !voteTarget.alive) {
      setMessage('该玩家已出局，请重新选择投票目标');
      return;
    }
    setLoading(true);
    setMessage('');
    try {
      const result = await gameApi.vote({
        game_id: gameState.game_id,
        player_id: playerId,
        target_id: selectedPlayerId,
      });
      // Show vote result dialog
      if (result.data?.vote_summary) {
        setVoteResult({
          vote_summary: result.data.vote_summary,
          eliminated: result.data.eliminated,
          tie: result.data.tie,
        });
      }
      const updated = await gameApi.getGameState(gameState.game_id, playerId);
      useGameStore.getState().setGameState(updated);
    } catch (error: any) {
      setMessage(`投票失败: ${error.response?.data?.detail || error.message}`);
    } finally {
      setLoading(false);
    }
  };

  // === Abstain Vote ===
  const handleAbstain = async () => {
    setLoading(true);
    setMessage('');
    try {
      const result = await gameApi.vote({
        game_id: gameState.game_id,
        player_id: playerId,
        target_id: null,
      });
      if (result.data?.vote_summary) {
        setVoteResult({
          vote_summary: result.data.vote_summary,
          eliminated: result.data.eliminated,
          tie: result.data.tie,
        });
      }
      const updated = await gameApi.getGameState(gameState.game_id, playerId);
      useGameStore.getState().setGameState(updated);
    } catch (error: any) {
      setMessage(`投票失败: ${error.response?.data?.detail || error.message}`);
    } finally {
      setLoading(false);
    }
  };

  // === Render helpers ===
  const TargetHint = () => {
    if (!selectedPlayerId) {
      return (
        <div className="flex items-center gap-2 text-amber-400 text-sm mb-3">
          <Target className="size-4" />
          <span>请在上方选择目标玩家</span>
        </div>
      );
    }
    return (
      <div className="flex items-center gap-2 text-emerald-400 text-sm mb-3">
        <CheckCircle2 className="size-4" />
        <span>目标: <strong>{selectedPlayerName}</strong></span>
      </div>
    );
  };

  const renderWitchPanel = () => {
    // Step 1: Load witch info
    if (!witchInfo) {
      return (
        <Button
          onClick={loadWitchInfo}
          disabled={witchLoading}
          size="lg"
          className="w-full gap-2 bg-emerald-800 hover:bg-emerald-700 text-white"
        >
          <Sparkles className="size-4" />
          {witchLoading ? '加载中...' : '查看今夜情况'}
        </Button>
      );
    }

    // Step 2: Show info and options
    return (
      <div className="space-y-3">
        {/* Potion status */}
        <div className="flex gap-2">
          <div className={cn(
            "flex-1 flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border",
            witchInfo.save_available
              ? "bg-emerald-900/40 border-emerald-700/40 text-emerald-300"
              : "bg-muted/30 border-border/30 text-muted-foreground line-through"
          )}>
            <Heart className="size-3.5" />
            解药 {witchInfo.save_available ? '可用' : '已用'}
          </div>
          <div className={cn(
            "flex-1 flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border",
            witchInfo.poison_available
              ? "bg-purple-900/40 border-purple-700/40 text-purple-300"
              : "bg-muted/30 border-border/30 text-muted-foreground line-through"
          )}>
            <Skull className="size-3.5" />
            毒药 {witchInfo.poison_available ? '可用' : '已用'}
          </div>
        </div>

        {/* Tonight's victim (only shown when save potion is available) */}
        {witchInfo.save_available && (
          <div className={cn(
            "px-3 py-2.5 rounded-lg text-sm border",
            witchInfo.victim_name
              ? "bg-red-950/30 border-red-800/40 text-red-300"
              : "bg-muted/30 border-border/30 text-muted-foreground"
          )}>
            {witchInfo.victim_name
              ? <>今夜 <strong>{witchInfo.victim_name}</strong> 被狼人杀害</>
              : '今夜是平安夜，无人被杀'}
          </div>
        )}

        {/* Action buttons */}
        <div className="space-y-2">
          {/* Save button */}
          {witchInfo.save_available && witchInfo.victim_name && (
            <Button
              onClick={() => handleWitchAction('save')}
              disabled={loading}
              size="lg"
              className="w-full gap-2 bg-emerald-800 hover:bg-emerald-700 text-white"
            >
              <Heart className="size-4" />
              {loading ? '处理中...' : `使用解药救 ${witchInfo.victim_name}`}
            </Button>
          )}

          {/* Poison button (requires target selection) */}
          {witchInfo.poison_available && (
            <>
              <TargetHint />
              <Button
                onClick={() => handleWitchAction('poison')}
                disabled={loading || !selectedPlayerId}
                size="lg"
                className="w-full gap-2 bg-purple-800 hover:bg-purple-700 text-white"
              >
                <Skull className="size-4" />
                {loading ? '处理中...' : '使用毒药毒杀'}
              </Button>
            </>
          )}

          {/* Skip button */}
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
    );
  };

  const renderGuardPanel = () => {
    // Step 1: Load guard info
    if (!guardInfo) {
      return (
        <Button
          onClick={loadGuardInfo}
          disabled={guardLoading}
          size="lg"
          className="w-full gap-2 bg-blue-800 hover:bg-blue-700 text-white"
        >
          <Shield className="size-4" />
          {guardLoading ? '加载中...' : '查看守护信息'}
        </Button>
      );
    }

    // Step 2: Show info and options
    return (
      <div className="space-y-3">
        {/* Last guarded player hint */}
        {guardInfo.last_guarded_name && (
          <div className="px-3 py-2.5 rounded-lg text-sm border bg-blue-950/30 border-blue-800/40 text-blue-300">
            上轮守护了 <strong>{guardInfo.last_guarded_name}</strong>，本轮不能再守护此人
          </div>
        )}

        <TargetHint />
        <div className="flex gap-2">
          <Button
            onClick={() => handleGuardAction(false)}
            disabled={loading || !selectedPlayerId}
            size="lg"
            className="flex-1 gap-2 bg-blue-800 hover:bg-blue-700 text-white"
          >
            <Shield className="size-4" />
            {loading ? '处理中...' : '守护此玩家'}
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
    );
  };

  // Enter night with transition animation (shown once per night cycle for all roles)
  const handleEnterNight = async () => {
    setLoading(true);
    setMessage('');
    // Play night transition animation
    useGameStore.getState().setPhaseTransition('night');
    await new Promise((r) => setTimeout(r, TRANSITION_DURATION));
    setNightEntered(true);
    // The useEffect (nightAutoAdvRef) handles auto-advancing non-player phases
    setLoading(false);
  };

  const renderNightActions = () => {
    // First entry to night: show "进入夜晚" button for ALL roles
    if (!nightEntered) {
      return (
        <Button
          onClick={handleEnterNight}
          disabled={loading}
          size="lg"
          className="w-full gap-2 bg-indigo-900 hover:bg-indigo-800 text-white"
        >
          <Moon className="size-4" />
          {loading ? '夜晚进行中...' : '进入夜晚'}
        </Button>
      );
    }

    // Night entered — show role-specific actions or advance button
    if (isMyNightTurn) {
      return (
        <div className="space-y-3">
          {phase === 'NIGHT_WEREWOLF' && !wolfDiscussed && (
            <Button
              onClick={handleWolfDiscuss}
              disabled={loading}
              size="lg"
              className="w-full gap-2 bg-red-900 hover:bg-red-800 text-white"
            >
              <MessageSquare className="size-4" />
              {loading ? (
                <>
                  <span className="size-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  队友思考中...
                </>
              ) : '开始狼人讨论'}
            </Button>
          )}
          {phase === 'NIGHT_WEREWOLF' && wolfDiscussed && (
            <div className="space-y-3">
              {/* Human wolf's night message */}
              <div>
                <label className="block text-sm text-red-300/80 mb-2">
                  <MessageSquare className="size-3.5 inline mr-1.5" />
                  你的夜间指令（给队友的消息）
                </label>
                <Textarea
                  value={wolfMessage}
                  onChange={(e) => setWolfMessage(e.target.value)}
                  placeholder="例如：你明天跳预言家，我配合投票..."
                  disabled={loading}
                  className="bg-red-950/30 border-red-800/30 resize-none focus-visible:ring-red-500/50 placeholder:text-red-300/30 text-red-100"
                  rows={2}
                />
              </div>

              {/* Target selection + kill button */}
              <TargetHint />
              <Button
                onClick={handleWolfSubmitAndKill}
                disabled={loading || !selectedPlayerId}
                variant="destructive"
                size="lg"
                className="w-full gap-2"
              >
                <Crosshair className="size-4" />
                {loading ? (
                  <>
                    <span className="size-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    处理中...
                  </>
                ) : '确认击杀目标'}
              </Button>
            </div>
          )}
          {phase === 'NIGHT_SEER' && (
            <>
              <TargetHint />
              <Button
                onClick={() => handleNightAction('seer_check')}
                disabled={loading}
                size="lg"
                className="w-full gap-2 bg-purple-800 hover:bg-purple-700 text-white"
              >
                <Eye className="size-4" />
                {loading ? '处理中...' : '查验此玩家'}
              </Button>
            </>
          )}
          {phase === 'NIGHT_WITCH' && renderWitchPanel()}
          {phase === 'NIGHT_GUARD' && renderGuardPanel()}
        </div>
      );
    }

    // Non-player night phase — auto-advancing via useEffect
    return (
      <Button
        disabled
        size="lg"
        className="w-full gap-2 bg-indigo-900 text-white"
      >
        <span className="size-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
        夜晚进行中...
      </Button>
    );
  };

  const renderDiscussion = () => {
    // If AI is speaking or log still animating, show waiting (except during human input)
    if (discussionStep !== 'awaiting_human' && (gameState.ai_speaking || !allEventsRevealed)) {
      return (
        <Button
          disabled
          size="lg"
          className="w-full gap-2 bg-amber-800 text-white"
        >
          <span className="size-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
          AI 发言中...
        </Button>
      );
    }

    // Step 1: Not started yet — show "开始讨论" button
    if (discussionStep === 'init') {
      return (
        <Button
          onClick={handlePreDiscussion}
          disabled={loading}
          size="lg"
          className="w-full gap-2 bg-amber-800 hover:bg-amber-700 text-white"
        >
          {loading ? (
            <>
              <span className="size-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              AI 发言中...
            </>
          ) : (
            <>
              <MessageSquare className="size-4" />
              开始讨论
            </>
          )}
        </Button>
      );
    }

    // Step 2: Human's turn to speak
    return (
      <div className="space-y-4">
        <div>
          <label className="block text-sm text-muted-foreground mb-2">
            <MessageSquare className="size-3.5 inline mr-1.5" />
            你的发言（身份: {ROLE_LABEL[currentPlayer.role] || '未知'}）
          </label>
          <Textarea
            value={speechText}
            onChange={(e) => setSpeechText(e.target.value)}
            placeholder="输入你的发言..."
            disabled={loading}
            className="bg-muted/50 border-border/50 resize-none focus-visible:ring-amber-500/50 placeholder:text-muted-foreground/50"
            rows={3}
          />
        </div>
        <Button
          onClick={handleSubmitAndFinish}
          disabled={loading}
          size="lg"
          className="w-full gap-2 bg-emerald-800 hover:bg-emerald-700 text-white"
        >
          {loading ? (
            <>
              <span className="size-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              AI 发言中...
            </>
          ) : (
            '提交发言并继续'
          )}
        </Button>
      </div>
    );
  };

  return (
    <Card className={cn("border", theme.border, theme.bg, "backdrop-blur-sm")}>
      <CardHeader className="pb-3">
        <CardTitle className="text-lg font-display tracking-wide flex items-center gap-2">
          {isNight && <Moon className="size-5 text-indigo-400" />}
          {phase === 'DAY_DISCUSSION' && <MessageSquare className="size-5 text-amber-400" />}
          {phase === 'DAY_VOTE' && <Vote className="size-5 text-orange-400" />}
          {phase === 'HUNTER_SHOOT' && <Crosshair className="size-5 text-orange-400" />}
          操作面板
        </CardTitle>
      </CardHeader>

      <Separator className="bg-border/30" />

      <CardContent className="pt-4">
        {/* Dead player hint */}
        {!isAlive && dayEntered && (phase === 'DAY_DISCUSSION' || phase === 'DAY_VOTE') && (
          <div className="flex items-center gap-2 text-muted-foreground text-sm mb-3">
            <AlertCircle className="size-4" />
            <span>你已出局，作为旁观者继续观战</span>
          </div>
        )}

        {/* Target hint for voting (alive only) */}
        {phase === 'DAY_VOTE' && dayEntered && isAlive && <TargetHint />}

        <div className="mb-3">
          {isNight && renderNightActions()}
          {phase.startsWith('DAY_') && !dayEntered && (
            <Button
              onClick={async () => {
                setLoading(true);
                useGameStore.getState().setPhaseTransition('day');
                await new Promise((r) => setTimeout(r, TRANSITION_DURATION));
                setDayEntered(true);
                setLoading(false);
              }}
              disabled={loading}
              size="lg"
              className="w-full gap-2 bg-amber-800 hover:bg-amber-700 text-white"
            >
              <Sun className="size-4" />
              {loading ? '天亮了...' : '进入白天'}
            </Button>
          )}
          {phase === 'DAY_DISCUSSION' && dayEntered && isAlive && renderDiscussion()}
          {phase === 'DAY_DISCUSSION' && dayEntered && !isAlive && (
            <Button
              onClick={handleAutoDiscussion}
              disabled={loading}
              size="lg"
              className="w-full gap-2 bg-amber-800 hover:bg-amber-700 text-white"
            >
              {loading ? (
                <>
                  <span className="size-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  AI 发言中...
                </>
              ) : (
                <>
                  <MessageSquare className="size-4" />
                  观看讨论
                </>
              )}
            </Button>
          )}
          {phase === 'DAY_VOTE' && dayEntered && isAlive && (
            <div className="space-y-2">
              <Button
                onClick={handleVote}
                disabled={loading || !selectedPlayerId || !allEventsRevealed}
                size="lg"
                className="w-full gap-2 bg-orange-800 hover:bg-orange-700 text-white"
              >
                {!allEventsRevealed ? (
                  <>
                    <span className="size-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
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
                onClick={handleAbstain}
                disabled={loading || !allEventsRevealed}
                variant="outline"
                size="lg"
                className="w-full gap-2 text-muted-foreground"
              >
                <Ban className="size-4" />
                弃权
              </Button>
            </div>
          )}
          {phase === 'DAY_VOTE' && dayEntered && !isAlive && (
            <Button
              onClick={handleAutoVote}
              disabled={loading || !allEventsRevealed}
              size="lg"
              className="w-full gap-2 bg-orange-800 hover:bg-orange-700 text-white"
            >
              {loading ? (
                <>
                  <span className="size-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  投票中...
                </>
              ) : !allEventsRevealed ? (
                <>
                  <span className="size-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  等待发言结束...
                </>
              ) : (
                <>
                  <Vote className="size-4" />
                  观看投票
                </>
              )}
            </Button>
          )}
          {phase === 'HUNTER_SHOOT' && currentPlayer.role === 'hunter' && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-orange-300 text-sm">
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
            <div className="flex items-center justify-center gap-2 text-muted-foreground text-sm py-4">
              <span className="size-4 border-2 border-muted-foreground/30 border-t-muted-foreground rounded-full animate-spin" />
              猎人正在选择目标...
            </div>
          )}
        </div>

        {message && (
          <div className={cn(
            "flex items-start gap-2 p-3 rounded-lg text-sm animate-fade-in",
            message.includes('失败')
              ? "bg-destructive/10 border border-destructive/30 text-red-300"
              : "bg-emerald-900/20 border border-emerald-800/30 text-emerald-300"
          )}>
            {message.includes('失败')
              ? <AlertCircle className="size-4 mt-0.5 shrink-0" />
              : <CheckCircle2 className="size-4 mt-0.5 shrink-0" />
            }
            <span>{message}</span>
          </div>
        )}
      </CardContent>

      {/* Vote Result Dialog */}
      <Dialog open={!!voteResult} onOpenChange={(open) => !open && setVoteResult(null)}>
        <DialogContent className="bg-card border-border/50 max-w-md">
          <DialogHeader>
            <DialogTitle className="text-lg font-display tracking-wide flex items-center gap-2">
              <Vote className="size-5 text-orange-400" />
              投票结果
            </DialogTitle>
          </DialogHeader>
          {voteResult && (
            <div className="space-y-3 mt-2">
              {Object.entries(voteResult.vote_summary)
                .sort(([, a], [, b]) => b.votes - a.votes)
                .map(([targetName, info]) => {
                  const isAbstain = targetName === '弃权';
                  const isEliminated = voteResult.eliminated === targetName;
                  return (
                    <div
                      key={targetName}
                      className={cn(
                        "rounded-lg border p-3",
                        isEliminated
                          ? "bg-red-950/30 border-red-800/50"
                          : isAbstain
                            ? "bg-muted/20 border-dashed border-border/40"
                            : "bg-muted/30 border-border/30"
                      )}
                    >
                      <div className="flex items-center justify-between mb-1.5">
                        <span className={cn(
                          "font-semibold",
                          isEliminated && "text-red-300",
                          isAbstain && "text-muted-foreground italic"
                        )}>
                          {isAbstain && <Ban className="size-3.5 inline mr-1.5" />}
                          {targetName}
                          {isEliminated && (
                            <span className="ml-2 text-xs text-red-400">出局</span>
                          )}
                        </span>
                        <span className={cn(
                          "text-sm font-medium px-2 py-0.5 rounded-full",
                          isEliminated
                            ? "bg-red-900/50 text-red-300"
                            : "bg-muted/50 text-muted-foreground"
                        )}>
                          {info.votes} 票
                        </span>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {info.voters.map((voter: string) => (
                          <span
                            key={voter}
                            className="text-xs px-2 py-0.5 rounded-full bg-muted/50 text-muted-foreground border border-border/30"
                          >
                            {voter}
                          </span>
                        ))}
                      </div>
                    </div>
                  );
                })}
              {voteResult.tie && (
                <div className="text-center text-amber-400 text-sm py-2">
                  平票，无人出局
                </div>
              )}
              <Button
                onClick={() => setVoteResult(null)}
                variant="outline"
                className="w-full mt-2"
              >
                确定
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </Card>
  );
}
