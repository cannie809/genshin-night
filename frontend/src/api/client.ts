import axios from 'axios'
import type {
  GameConfig,
  GameState,
  NightActionRequest,
  VoteRequest,
  SpeechRequest,
  ActionResult,
} from '../types/game'
import type { RoomView } from '../types/room'
import { useGameStore } from '../store/gameStore'

// Use same origin (frontend served from FastAPI backend)
export const apiClient = axios.create({
  baseURL: '',
  headers: {
    'Content-Type': 'application/json',
  },
})

// Auth: every /api/* request carries the client's stable UUID as the Bearer
// token. Backend trusts this claim (anonymous multi-player).
apiClient.interceptors.request.use((config) => {
  const identity = useGameStore.getState().identity
  if (identity) {
    config.headers.Authorization = `Bearer ${identity}`
  }
  return config
})

// Character pool for landing page
export interface CharacterInfo {
  id: string
  name: string
  avatar_url: string
}

// Game API
export const gameApi = {
  /** GET /api/characters */
  getCharacters: async (): Promise<CharacterInfo[]> => {
    const response = await apiClient.get('/api/characters')
    return response.data
  },

  /**
   * Start a new game (legacy single-player; rooms use roomApi.start instead).
   * POST /api/game/start
   */
  startGame: async (config: GameConfig = {}): Promise<GameState> => {
    const response = await apiClient.post('/api/game/start', config)
    return response.data
  },

  /**
   * Get current game state
   * GET /api/game/state/{game_id}?player_id=player_0
   */
  getGameState: async (gameId: string, playerId: string = 'player_0'): Promise<GameState> => {
    const response = await apiClient.get(`/api/game/state/${gameId}`, {
      params: { player_id: playerId },
    })
    return response.data
  },

  /**
   * Get witch info (victim, potion status)
   * GET /api/game/witch-info/{game_id}
   */
  witchInfo: async (
    gameId: string,
    playerId: string,
  ): Promise<{
    victim_name: string | null
    save_available: boolean
    poison_available: boolean
  }> => {
    const response = await apiClient.get(`/api/game/witch-info/${gameId}`, {
      params: { player_id: playerId },
    })
    return response.data
  },

  /**
   * Get guard info (last guarded player)
   * GET /api/game/guard-info/{game_id}
   */
  guardInfo: async (
    gameId: string,
    playerId: string,
  ): Promise<{
    last_guarded_name: string | null
  }> => {
    const response = await apiClient.get(`/api/game/guard-info/${gameId}`, {
      params: { player_id: playerId },
    })
    return response.data
  },

  /**
   * Process night action
   * POST /api/game/night-action
   */
  nightAction: async (request: NightActionRequest): Promise<ActionResult> => {
    const response = await apiClient.post('/api/game/night-action', request)
    return response.data
  },

  /**
   * Generate AI player speech
   * POST /api/game/ai-speak
   */
  aiSpeak: async (request: SpeechRequest): Promise<ActionResult> => {
    const response = await apiClient.post('/api/game/ai-speak', request)
    return response.data
  },

  /**
   * Process human player vote
   * POST /api/game/vote
   * Returns blind progress {submitted, total, waiting:true} until all alive
   * humans have voted; after last human, runs AI votes + resolves.
   */
  vote: async (request: VoteRequest): Promise<ActionResult> => {
    const response = await apiClient.post('/api/game/vote', request)
    return response.data
  },

  /**
   * Generate AI player votes
   * POST /api/game/ai-vote
   */
  aiVote: async (request: SpeechRequest): Promise<ActionResult> => {
    const response = await apiClient.post('/api/game/ai-vote', request)
    return response.data
  },

  /**
   * Process hunter shooting
   * POST /api/game/hunter-shoot
   */
  hunterShoot: async (request: NightActionRequest): Promise<ActionResult> => {
    const response = await apiClient.post('/api/game/hunter-shoot', request)
    return response.data
  },

  /**
   * Get AI wolf partner's suggestion (for human werewolf)
   * POST /api/game/wolf-discuss
   */
  wolfDiscuss: async (request: SpeechRequest): Promise<GameState> => {
    const response = await apiClient.post('/api/game/wolf-discuss', request)
    return response.data
  },

  /**
   * Submit human wolf's night message to teammates
   * POST /api/game/wolf-human-speak
   */
  wolfHumanSpeak: async (request: {
    game_id: string
    player_id: string
    content: string
  }): Promise<GameState> => {
    const response = await apiClient.post('/api/game/wolf-human-speak', request)
    return response.data
  },

  /**
   * Drive AI night sub-phases. Night-ack-gated: if any alive human hasn't
   * called /enter-night this round, returns the current state unchanged
   * and does NOT run AI. Safe to retry on poll.
   * POST /api/game/advance-night
   */
  advanceNight: async (request: SpeechRequest): Promise<GameState> => {
    const response = await apiClient.post('/api/game/advance-night', request)
    return response.data
  },

  /**
   * Record this human's explicit "进入白天" acknowledgement. Gates
   * /advance-discussion. Separate endpoint so the auto-advance loop
   * doesn't passively consume the caller's morning-ack.
   * POST /api/game/enter-day
   */
  enterDay: async (request: SpeechRequest): Promise<GameState> => {
    const response = await apiClient.post('/api/game/enter-day', request)
    return response.data
  },

  /**
   * Record this human's explicit "进入夜晚" acknowledgement. Gates
   * /advance-night + all night action endpoints.
   * POST /api/game/enter-night
   */
  enterNight: async (request: SpeechRequest): Promise<GameState> => {
    const response = await apiClient.post('/api/game/enter-night', request)
    return response.data
  },

  /**
   * Submit human player speech.
   * - 403 NOT_YOUR_SLOT: player_id doesn't match caller's identity.
   * - 409 NOT_YOUR_TURN: caller is not current_speaker_id. Returns
   *   {current_speaker_id, your_player_id}. Swallow + re-fetch state.
   * POST /api/game/human-speak
   */
  humanSpeak: async (request: {
    game_id: string
    player_id: string
    content: string
  }): Promise<ActionResult> => {
    const response = await apiClient.post('/api/game/human-speak', request)
    return response.data
  },

  /**
   * Unified discussion advancer. Replaces /pre-discussion + /finish-discussion.
   * Call on DAY_DISCUSSION entry AND after humanSpeak succeeds. Backend runs
   * AI speeches until next human or phase end. Idempotent to retry.
   * POST /api/game/advance-discussion
   */
  advanceDiscussion: async (request: SpeechRequest): Promise<GameState> => {
    const response = await apiClient.post('/api/game/advance-discussion', request)
    return response.data
  },

  /**
   * End current round; records this caller's night-ack.
   * POST /api/game/end-round
   */
  endRound: async (request: SpeechRequest): Promise<ActionResult> => {
    const response = await apiClient.post('/api/game/end-round', request)
    return response.data
  },
}

// --- Room API ---
// Thin wrapper over /api/rooms/* endpoints. All return the caller's RoomView
// (preferred_role scrubbed for non-self players, etc.).

interface RoomsListResponse {
  rooms: RoomView[]
}

export const roomApi = {
  list: async (): Promise<RoomView[]> => {
    const resp = await apiClient.get<RoomsListResponse>('/api/rooms')
    return resp.data.rooms
  },

  get: async (id: string): Promise<RoomView> => {
    const resp = await apiClient.get<RoomView>(`/api/rooms/${id}`)
    return resp.data
  },

  join: async (id: string, displayName: string): Promise<RoomView> => {
    const resp = await apiClient.post<RoomView>(`/api/rooms/${id}/join`, {
      display_name: displayName,
    })
    return resp.data
  },

  leave: async (id: string): Promise<RoomView> => {
    const resp = await apiClient.post<RoomView>(`/api/rooms/${id}/leave`)
    return resp.data
  },

  setRole: async (id: string, preferred_role: string | null): Promise<RoomView> => {
    const resp = await apiClient.post<RoomView>(`/api/rooms/${id}/set-role`, {
      preferred_role,
    })
    return resp.data
  },

  setMode: async (id: string, mode: string): Promise<RoomView> => {
    const resp = await apiClient.post<RoomView>(`/api/rooms/${id}/set-mode`, { mode })
    return resp.data
  },

  setReady: async (id: string, ready: boolean): Promise<RoomView> => {
    const resp = await apiClient.post<RoomView>(`/api/rooms/${id}/set-ready`, { ready })
    return resp.data
  },

  start: async (id: string): Promise<RoomView> => {
    const resp = await apiClient.post<RoomView>(`/api/rooms/${id}/start`)
    return resp.data
  },

  restart: async (id: string): Promise<RoomView> => {
    const resp = await apiClient.post<RoomView>(`/api/rooms/${id}/restart`)
    return resp.data
  },
}
