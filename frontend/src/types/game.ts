export type Role = 'villager' | 'werewolf' | 'seer' | 'witch' | 'hunter'

export type Phase = 'night' | 'day' | 'voting' | 'ended'

export type PlayerStatus = 'alive' | 'dead'

export interface Player {
  id: string
  name: string
  role: Role | '???' // '???' when role is hidden
  alive: boolean
  is_human: boolean
  avatar_url?: string | null
  seer_result?: string | null // "WEREWOLF" or "GOOD" if checked by seer
  // For human players only — backend echoes these so the client can match its
  // stored identity to a specific player slot. Null for AI players.
  identity?: string | null
  display_name?: string | null
}

export interface GameEvent {
  type: string
  round: number
  phase: string
  message: string
  data?: Record<string, any>
}

// Caller's own pending night-action intent (Option B barrier). Surfaced by
// backend ONLY to the submitter — never leaks other players' pending
// targets. Goes back to null once `/enter-night` quorum is met and the
// backend auto-replays the stored intent (phase advances naturally).
export type CallerNightIntentKind =
  | 'seer_check'
  | 'guard_protect'
  | 'witch_action'
  | 'wolf_kill'

export interface WitchIntentDetail {
  use_save: boolean
  use_poison: boolean
  poison_target_id: string | null
}

export interface CallerNightIntent {
  kind: CallerNightIntentKind
  // null = skip (guard 空守, witch no-op). For witch, this mirrors
  // detail.poison_target_id for convenience.
  target_id: string | null
  // Present only when kind === 'witch_action'.
  detail?: WitchIntentDetail
}

export interface GameState {
  game_id: string
  mode: string
  phase: string
  round_number: number
  day_number: number
  players: Player[]
  events: GameEvent[]
  winner: string | null
  ai_speaking: boolean
  // Multi-human coordination fields from backend. In single-player these are
  // still populated (acks_needed=1, current_speaker_id may point at the
  // lone human slot).
  morning_acks: number
  morning_acks_needed: number
  night_acks: number
  night_acks_needed: number
  current_speaker_id: string | null
  // Blind vote progress: never exposes who voted for whom. UI shows
  // "已投票 N/M" until DAY_VOTE resolves and the vote_result event lands.
  vote_submitted: number
  vote_total: number
  // Explicit per-caller barrier flags. True iff the viewer's own identity
  // has recorded a morning/night ack this round. Used to recover UI state
  // after page reload so we never show "进入白天/夜晚" to a user whose ack
  // was already recorded server-side.
  caller_morning_acked: boolean
  caller_night_acked: boolean
  // Human-wolf intent barrier progress. `wolf_kill_submitted`/`wolf_kill_total`
  // mirror how many alive human wolves have locked in a target (the backend
  // only commits the kill once all humans submit). `caller_wolf_kill_submitted`
  // is true if the viewer (if a wolf) has already locked in.
  wolf_kill_submitted: number
  wolf_kill_total: number
  caller_wolf_kill_submitted: boolean
  // Caller's own pending night-action intent under the Option B barrier.
  // Null when there is no pending intent for this caller (either because
  // they haven't clicked, or quorum was met and the intent was replayed).
  caller_night_intent?: CallerNightIntent | null
  // Pure-spectator vote-watch barrier (all humans dead). Shows whether
  // this caller has acked "观看投票" and how many total spectators have
  // so the first clicker gets "已观看，等待 X" feedback instead of a
  // button that looks unchanged.
  caller_vote_watch_acked?: boolean
  vote_watch_submitted?: number
  vote_watch_total?: number
}

// Request types
export interface GameConfig {
  mode?: string
  preferred_role?: string | null
}

export interface NightActionRequest {
  game_id: string
  player_id: string
  action_type: string
  target_id?: string
  use_save?: boolean
  use_poison?: boolean
  poison_target_id?: string
}

export interface VoteRequest {
  game_id: string
  player_id: string
  target_id: string | null
}

export interface SpeechRequest {
  game_id: string
  player_id: string
}

// Night-action responses under Option B carry a richer `data` payload:
// - pending intent (waiting on other humans' /enter-night acks)
// - already-processed no-op (intent was already replayed by /enter-night)
// - legacy `waiting` flag used by the wolf-kill intent barrier
// Any other endpoint may still stuff arbitrary fields in here.
export interface ActionResultData {
  // Option B: intent stored, quorum not yet met.
  pending?: boolean
  reason?: string
  action_type?: string
  target_id?: string | null
  waiting_for?: string[]
  acked?: number
  needed?: number
  // Idempotent re-click after quorum-met-and-intent-replayed.
  already_processed?: boolean
  current_phase?: string
  // Existing human-wolf intent barrier flag.
  waiting?: boolean
  // Allow forward-compatible extra fields without losing type safety
  // at call sites that know what they're looking for.
  [key: string]: unknown
}

export interface ActionResult {
  success: boolean
  message: string
  data: ActionResultData
}
