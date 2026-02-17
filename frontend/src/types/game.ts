export type Role = 'villager' | 'werewolf' | 'seer' | 'witch' | 'hunter';

export type Phase = 'night' | 'day' | 'voting' | 'ended';

export type PlayerStatus = 'alive' | 'dead';

export interface Player {
  id: string;
  name: string;
  role: Role | '???';  // '???' when role is hidden
  alive: boolean;
  is_human: boolean;
  avatar_url?: string | null;
  seer_result?: string | null;  // "WEREWOLF" or "GOOD" if checked by seer
}

export interface GameEvent {
  type: string;
  round: number;
  phase: string;
  message: string;
  data?: Record<string, any>;
}

export interface GameState {
  game_id: string;
  mode: string;
  phase: string;
  round_number: number;
  day_number: number;
  players: Player[];
  events: GameEvent[];
  winner: string | null;
  ai_speaking: boolean;
}

// Request types
export interface GameConfig {
  mode?: string;
  preferred_role?: string | null;
}

export interface NightActionRequest {
  game_id: string;
  player_id: string;
  action_type: string;
  target_id?: string;
  use_save?: boolean;
  use_poison?: boolean;
  poison_target_id?: string;
}

export interface VoteRequest {
  game_id: string;
  player_id: string;
  target_id: string | null;
}

export interface SpeechRequest {
  game_id: string;
  player_id: string;
}

export interface ActionResult {
  success: boolean;
  message: string;
  data: any;
}
