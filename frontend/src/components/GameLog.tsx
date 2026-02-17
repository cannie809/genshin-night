import { useEffect, useRef, useState, useCallback } from 'react';
import { ScrollText, Moon, Sun, Crosshair, Vote, MessageSquare, Skull, AlertTriangle, Search } from 'lucide-react';
import { useGameStore } from '../store/gameStore';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { cn } from '@/lib/utils';
import type { GameEvent } from '../types/game';

const eventConfig: Record<string, {
  icon: typeof Moon;
  borderColor: string;
  iconColor: string;
}> = {
  death: { icon: Skull, borderColor: 'border-l-red-600', iconColor: 'text-red-400' },
  kill: { icon: Crosshair, borderColor: 'border-l-red-700', iconColor: 'text-red-500' },
  werewolf_chat: { icon: MessageSquare, borderColor: 'border-l-red-800', iconColor: 'text-red-400' },
  vote_result: { icon: Vote, borderColor: 'border-l-orange-600', iconColor: 'text-orange-400' },
  vote: { icon: Vote, borderColor: 'border-l-orange-600', iconColor: 'text-orange-400' },
  speech: { icon: MessageSquare, borderColor: 'border-l-amber-600', iconColor: 'text-amber-400' },
  discussion: { icon: MessageSquare, borderColor: 'border-l-amber-600', iconColor: 'text-amber-400' },
  night: { icon: Moon, borderColor: 'border-l-indigo-600', iconColor: 'text-indigo-400' },
  day: { icon: Sun, borderColor: 'border-l-amber-500', iconColor: 'text-amber-400' },
  seer: { icon: Moon, borderColor: 'border-l-purple-600', iconColor: 'text-purple-400' },
  witch: { icon: Moon, borderColor: 'border-l-emerald-600', iconColor: 'text-emerald-400' },
  warning: { icon: AlertTriangle, borderColor: 'border-l-yellow-600', iconColor: 'text-yellow-400' },
};

function getEventStyle(eventType: string) {
  const type = eventType.toLowerCase();
  for (const [key, config] of Object.entries(eventConfig)) {
    if (type.includes(key)) return config;
  }
  return { icon: Moon, borderColor: 'border-l-border', iconColor: 'text-muted-foreground' };
}

// Reveal delays
const OTHER_REVEAL_DELAY = 150;

// Speech animation timing
const MIN_TYPING_MS = 3000;      // minimum typing-dots duration (simulate "thinking")
const TYPEWRITER_INTERVAL = 30;  // ms between character reveal ticks
const TYPEWRITER_CHARS = 2;      // characters revealed per tick

/** Estimate total animation time for a speech message (typing dots + typewriter). */
function estimateSpeechAnimMs(message: string): number {
  const nameMatch = message.match(/^【.+?】[：:]\s*/);
  const contentLen = nameMatch ? message.length - nameMatch[0].length : message.length;
  return MIN_TYPING_MS + Math.ceil(contentLen / TYPEWRITER_CHARS) * TYPEWRITER_INTERVAL + 200;
}

/** Check if a speech message belongs to a specific player. */
function isSpeechFrom(message: string, name: string | undefined): boolean {
  return !!name && message.startsWith(`【${name}】`);
}

export function GameLog() {
  const { gameState, playerId, setAllEventsRevealed, allEventsRevealed } = useGameStore();
  const humanName = gameState?.players.find(p => p.id === playerId)?.name;
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollAreaRef = useRef<HTMLDivElement>(null);

  const events = gameState?.events || [];

  // --- Staggered reveal logic ---
  const [visibleCount, setVisibleCount] = useState(0);
  // Track total events we've "accepted" for queueing
  const acceptedRef = useRef(0);

  // --- Jump-to-event ---
  const [jumpInput, setJumpInput] = useState('');
  const [showJump, setShowJump] = useState(false);

  const handleJump = useCallback((value: string) => {
    const num = parseInt(value, 10);
    if (isNaN(num) || num < 1 || num > events.length) return;
    // data-event-index stores original 0-based index
    const target = scrollAreaRef.current?.querySelector(`[data-event-index="${num - 1}"]`);
    if (target) {
      target.scrollIntoView({ behavior: 'smooth', block: 'center' });
      target.classList.add('ring-1', 'ring-accent');
      setTimeout(() => target.classList.remove('ring-1', 'ring-accent'), 1500);
    }
    setShowJump(false);
    setJumpInput('');
  }, [events.length]);

  const aiSpeaking = gameState?.ai_speaking ?? false;

  // --- Animation-aware sequential reveal ---
  // Events with index >= animBoundary get typing + typewriter animation
  const animBoundaryRef = useRef(0);
  // Timestamp when the next event is allowed to appear (ensures sequential animation)
  const nextAvailableRef = useRef(0);

  // When events arrive, schedule reveals with animation-aware timing
  useEffect(() => {
    const total = events.length;
    if (total <= acceptedRef.current) return;

    const prevAccepted = acceptedRef.current;
    acceptedRef.current = total;

    // First load: show all instantly, no animation
    if (prevAccepted === 0) {
      setVisibleCount(total);
      animBoundaryRef.current = total; // everything before this is "old"
      setAllEventsRevealed(true);
      return;
    }

    setAllEventsRevealed(false);

    // Schedule each new event to appear sequentially
    const now = Date.now();
    let startTime = Math.max(now, nextAvailableRef.current);

    for (let i = prevAccepted; i < total; i++) {
      const ev = events[i];
      const isNewSpeech = ev?.type === 'speech' && i >= animBoundaryRef.current;
      const isHumanSpeech = isNewSpeech && isSpeechFrom(ev.message, humanName);
      const isSpeechAnim = isNewSpeech && !isHumanSpeech;
      const delay = Math.max(0, startTime - now);
      const targetIdx = i + 1;

      setTimeout(() => {
        setVisibleCount((prev) => Math.max(prev, targetIdx));
      }, delay);

      // AI speeches: animation duration + 500ms gap; human speeches & others: short fixed delay
      startTime += isSpeechAnim ? estimateSpeechAnimMs(ev.message) + 500 : OTHER_REVEAL_DELAY;
    }

    nextAvailableRef.current = startTime;
  }, [events.length]);

  // Mark allEventsRevealed when: ai done + all visible + animations estimated complete
  useEffect(() => {
    if (!aiSpeaking && visibleCount >= events.length && events.length > 0) {
      const now = Date.now();
      const waitTime = Math.max(0, nextAvailableRef.current - now);
      if (waitTime <= 0) {
        setAllEventsRevealed(true);
      } else {
        const timer = setTimeout(() => setAllEventsRevealed(true), waitTime);
        return () => clearTimeout(timer);
      }
    }
  }, [aiSpeaking, visibleCount, events.length]);

  // Auto-scroll to bottom on new visible events
  useEffect(() => {
    requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    });
  }, [visibleCount]);

  // Keep scrolling during typewriter animations (content height grows)
  useEffect(() => {
    if (allEventsRevealed) return;
    const timer = setInterval(() => {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, 200);
    return () => clearInterval(timer);
  }, [allEventsRevealed]);

  if (!gameState) return null;

  // Show only events from the last 2 rounds for readability
  const currentRound = gameState.round_number || 0;
  const displayMinRound = Math.max(0, currentRound - 1);

  // Build display list: only recent rounds, but preserve original indices
  // Hunter identity events: only show if hunter actually shot (not held fire)
  const hunterShot = events.some((e: GameEvent) => e.type === 'hunter_shoot');
  const hunterIdentityTypes = new Set(['hunter_death_night', 'hunter_death_vote', 'hunter_hold_fire']);
  const displayEvents: { event: GameEvent; origIdx: number }[] = [];
  for (let i = 0; i < visibleCount && i < events.length; i++) {
    if (events[i].round >= displayMinRound) {
      // Hide hunter identity events unless hunter actually shot
      if (hunterIdentityTypes.has(events[i].type) && !hunterShot) continue;
      displayEvents.push({ event: events[i], origIdx: i });
    }
  }
  const hiddenCount = visibleCount - displayEvents.length;

  return (
    <Card className="border-border/50 bg-card/80 backdrop-blur-sm h-fit">
      <CardHeader className="pb-3">
        <CardTitle className="text-lg font-display tracking-wide flex items-center gap-2">
          <ScrollText className="size-5 text-muted-foreground" />
          游戏日志
          {events.length > 0 && (
            <div className="ml-auto flex items-center gap-1">
              {showJump ? (
                <input
                  type="number"
                  min={1}
                  max={events.length}
                  value={jumpInput}
                  onChange={(e) => setJumpInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleJump(jumpInput);
                    if (e.key === 'Escape') { setShowJump(false); setJumpInput(''); }
                  }}
                  onBlur={() => { setShowJump(false); setJumpInput(''); }}
                  autoFocus
                  placeholder={`1-${events.length}`}
                  className="w-16 h-6 text-[11px] text-center bg-muted/60 border border-border/50 rounded px-1 outline-none focus:border-accent/50"
                />
              ) : (
                <button
                  onClick={() => setShowJump(true)}
                  className="flex items-center gap-1 h-6 px-2 text-[10px] bg-muted/50 text-muted-foreground border border-border/50 rounded hover:bg-muted/80 transition-colors"
                  title="跳转到第N条日志"
                >
                  <Search className="size-3" />
                  {events.length}条
                </button>
              )}
            </div>
          )}
        </CardTitle>
      </CardHeader>

      <Separator className="bg-border/30" />

      <CardContent className="pt-4 px-3">
        <ScrollArea className="h-[400px] pr-2" ref={scrollAreaRef}>
          {events.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-32 text-muted-foreground">
              <Moon className="size-8 mb-2 opacity-30" />
              <p className="text-sm">夜幕降临，故事即将开始...</p>
            </div>
          ) : (
            <div className="space-y-2">
              {hiddenCount > 0 && (
                <div className="text-center text-[11px] text-muted-foreground/50 py-1">
                  已隐藏 {hiddenCount} 条早期记录
                </div>
              )}
              {displayEvents.map(({ event, origIdx }) => (
                <EventItem
                  key={`${event.round}-${event.type}-${origIdx}`}
                  event={event}
                  index={origIdx}
                  shouldAnimate={origIdx >= animBoundaryRef.current && !isSpeechFrom(event.message, humanName)}
                />
              ))}

              {/* No separate indicator — SpeechContent typing dots handle "thinking" visual */}
              <div ref={bottomRef} />
            </div>
          )}
        </ScrollArea>
      </CardContent>
    </Card>
  );
}

const EVENT_TYPE_LABELS: Record<string, string> = {
  werewolf_chat: '狼人密语',
  werewolf_kill: '狼人行动',
  speech: '发言',
  vote_result: '投票结果',
  morning_death: '天亮公告',
  morning_safe: '天亮公告',
  seer_check: '预言家查验',
  witch_save: '女巫行动',
  witch_poison: '女巫行动',
  hunter_shoot: '猎人开枪',
  hunter_death_night: '猎人遗言',
  hunter_death_vote: '猎人遗言',
  game_start: '游戏开始',
  game_end: '游戏结束',
};

function eventTypeLabel(type: string): string {
  return EVENT_TYPE_LABELS[type] || type;
}

/** Bouncing dots — chat-app "typing" indicator. */
function TypingDots() {
  return (
    <span className="inline-flex items-center gap-1 ml-1 align-middle">
      {[0, 150, 300].map((delay) => (
        <span
          key={delay}
          className="inline-block size-1.5 rounded-full bg-muted-foreground/50 animate-bounce"
          style={{ animationDelay: `${delay}ms`, animationDuration: '1.2s' }}
        />
      ))}
    </span>
  );
}

/** Speech message with typing-dots → typewriter animation. */
function SpeechContent({ message, sticker, speakerName }: { message: string; sticker?: string; speakerName?: string }) {
  const [phase, setPhase] = useState<'typing' | 'revealing' | 'done'>('typing');
  const [charCount, setCharCount] = useState(0);
  const stickerFiredRef = useRef(false);

  const { addStickerBubble, removeStickerBubble } = useGameStore();

  // Split 【name】: prefix from actual speech content
  const nameMatch = message.match(/^(【.+?】[：:]\s*)/);
  const prefix = nameMatch ? nameMatch[1] : '';
  const content = nameMatch ? message.slice(prefix.length) : message;

  // Phase 1: show typing dots for a minimum duration
  useEffect(() => {
    const timer = setTimeout(() => setPhase('revealing'), MIN_TYPING_MS);
    return () => clearTimeout(timer);
  }, []);

  // Fire sticker bubble when transitioning to 'revealing'
  useEffect(() => {
    if (phase === 'revealing' && sticker && speakerName && !stickerFiredRef.current) {
      stickerFiredRef.current = true;
      const bubbleId = addStickerBubble(speakerName, sticker);
      // Auto-remove after 4 seconds
      setTimeout(() => removeStickerBubble(bubbleId), 4000);
    }
  }, [phase, sticker, speakerName]);

  // Phase 2: typewriter — reveal characters progressively
  useEffect(() => {
    if (phase !== 'revealing') return;
    if (charCount >= content.length) {
      setPhase('done');
      return;
    }
    const timer = setTimeout(
      () => setCharCount((prev) => Math.min(prev + TYPEWRITER_CHARS, content.length)),
      TYPEWRITER_INTERVAL,
    );
    return () => clearTimeout(timer);
  }, [phase, charCount, content.length]);

  if (phase === 'typing') {
    return (
      <p className="text-xs text-muted-foreground leading-relaxed">
        {prefix}<TypingDots />
      </p>
    );
  }

  return (
    <p className="text-xs text-muted-foreground leading-relaxed">
      {prefix}
      {phase === 'done' ? content : content.slice(0, charCount)}
      {phase === 'revealing' && (
        <span className="inline-block w-px h-3 bg-muted-foreground/60 ml-px animate-pulse align-middle" />
      )}
    </p>
  );
}

function EventItem({ event, index, shouldAnimate }: { event: GameEvent; index: number; shouldAnimate?: boolean }) {
  const style = getEventStyle(event.type);
  const Icon = style.icon;
  const isSpeechAnim = shouldAnimate && event.type === 'speech';

  return (
    <div
      data-event-index={index}
      className={cn(
        "bg-muted/30 rounded-lg p-3 border-l-2 animate-message-in transition-all",
        style.borderColor,
      )}
    >
      <div className="flex items-start gap-2">
        <Icon className={cn("size-3.5 mt-0.5 shrink-0", style.iconColor)} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2 mb-1">
            <span className="text-xs font-medium text-foreground/80 truncate">
              {eventTypeLabel(event.type)}
            </span>
            <span className="text-[10px] text-muted-foreground whitespace-nowrap">
              #{index + 1}
            </span>
          </div>
          {/* Render vote_result with structured breakdown */}
          {event.type === 'vote_result' && event.data?.vote_summary ? (
            <div className="space-y-1.5">
              {Object.entries(event.data.vote_summary as Record<string, { votes: number; voters: string[] }>)
                .sort(([, a], [, b]) => b.votes - a.votes)
                .map(([targetName, info]) => {
                  const isAbstain = targetName === '弃权';
                  return (
                    <div key={targetName} className="text-xs">
                      <span className={cn(
                        "font-medium",
                        event.data?.eliminated_name === targetName && "text-red-400",
                        isAbstain && "text-muted-foreground/60 italic"
                      )}>
                        {targetName}
                      </span>
                      <span className="text-muted-foreground">
                        {' '}{info.votes}票 ←{' '}
                        {info.voters.join('、')}
                      </span>
                      {event.data?.eliminated_name === targetName && !isAbstain && (
                        <span className="text-red-400 ml-1">💀</span>
                      )}
                    </div>
                  );
                })}
              {event.data?.tie && (
                <div className="text-xs text-amber-400">平票，无人出局</div>
              )}
            </div>
          ) : isSpeechAnim ? (
            <SpeechContent
              message={event.message}
              sticker={event.data?.sticker}
              speakerName={event.data?.speaker_name}
            />
          ) : (
            <p className="text-xs text-muted-foreground leading-relaxed">
              {event.message}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
