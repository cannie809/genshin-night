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

export interface ActionResult {
  success: boolean
  message: string
  data: any
}
