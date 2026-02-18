import axios from 'axios';
import type {
  GameConfig,
  GameState,
  NightActionRequest,
  VoteRequest,
  SpeechRequest,
  ActionResult,
} from '../types/game';
import { useGameStore } from '../store/gameStore';

// Use same origin (frontend served from FastAPI backend)
export const apiClient = axios.create({
  baseURL: '',
  headers: {
    'Content-Type': 'application/json',
  },
});

apiClient.interceptors.request.use((config) => {
    const key = useGameStore.getState().accessKey;
    if (key) {
        config.headers.Authorization = `Bearer ${key}`;
    }
    return config;
});

apiClient.interceptors.response.use(
    (resp) => resp,
    (err) => {
        if (err.response?.status === 401) {
            useGameStore.getState().setAccessKey(null);
            useGameStore.getState().setPlayerName(null);
        }
        return Promise.reject(err);
    }
);

// Character pool for landing page
export interface CharacterInfo {
  id: string;
  name: string;
  avatar_url: string;
}

// Game API
export const gameApi = {
  /** POST /api/verify-key */
  verifyKey: async (): Promise<{ status: string; player_name: string }> => {
    const resp = await apiClient.post('/api/verify-key');
    return resp.data;
  },

  /** GET /api/characters */
  getCharacters: async (): Promise<CharacterInfo[]> => {
    const response = await apiClient.get('/api/characters');
    return response.data;
  },

  /**
   * Start a new game
   * POST /api/game/start
   */
  startGame: async (config: GameConfig = {}): Promise<GameState> => {
    const response = await apiClient.post('/api/game/start', config);
    return response.data;
  },

  /**
   * Get current game state
   * GET /api/game/state/{game_id}?player_id=player_0
   */
  getGameState: async (gameId: string, playerId: string = 'player_0'): Promise<GameState> => {
    const response = await apiClient.get(`/api/game/state/${gameId}`, {
      params: { player_id: playerId },
    });
    return response.data;
  },

  /**
   * Get witch info (victim, potion status)
   * GET /api/game/witch-info/{game_id}
   */
  witchInfo: async (gameId: string, playerId: string): Promise<{
    victim_name: string | null;
    save_available: boolean;
    poison_available: boolean;
  }> => {
    const response = await apiClient.get(`/api/game/witch-info/${gameId}`, {
      params: { player_id: playerId },
    });
    return response.data;
  },

  /**
   * Get guard info (last guarded player)
   * GET /api/game/guard-info/{game_id}
   */
  guardInfo: async (gameId: string, playerId: string): Promise<{
    last_guarded_name: string | null;
  }> => {
    const response = await apiClient.get(`/api/game/guard-info/${gameId}`, {
      params: { player_id: playerId },
    });
    return response.data;
  },

  /**
   * Process night action
   * POST /api/game/night-action
   */
  nightAction: async (request: NightActionRequest): Promise<ActionResult> => {
    const response = await apiClient.post('/api/game/night-action', request);
    return response.data;
  },

  /**
   * Generate AI player speech
   * POST /api/game/ai-speak
   */
  aiSpeak: async (request: SpeechRequest): Promise<ActionResult> => {
    const response = await apiClient.post('/api/game/ai-speak', request);
    return response.data;
  },

  /**
   * Process human player vote
   * POST /api/game/vote
   */
  vote: async (request: VoteRequest): Promise<ActionResult> => {
    const response = await apiClient.post('/api/game/vote', request);
    return response.data;
  },

  /**
   * Generate AI player votes
   * POST /api/game/ai-vote
   */
  aiVote: async (request: SpeechRequest): Promise<ActionResult> => {
    const response = await apiClient.post('/api/game/ai-vote', request);
    return response.data;
  },

  /**
   * Process hunter shooting
   * POST /api/game/hunter-shoot
   */
  hunterShoot: async (request: NightActionRequest): Promise<ActionResult> => {
    const response = await apiClient.post('/api/game/hunter-shoot', request);
    return response.data;
  },

  /**
   * Get AI wolf partner's suggestion (for human werewolf)
   * POST /api/game/wolf-discuss
   */
  wolfDiscuss: async (request: SpeechRequest): Promise<GameState> => {
    const response = await apiClient.post('/api/game/wolf-discuss', request);
    return response.data;
  },

  /**
   * Submit human wolf's night message to teammates
   * POST /api/game/wolf-human-speak
   */
  wolfHumanSpeak: async (request: { game_id: string; player_id: string; content: string }): Promise<GameState> => {
    const response = await apiClient.post('/api/game/wolf-human-speak', request);
    return response.data;
  },

  /**
   * Advance through AI night phases automatically
   * POST /api/game/advance-night
   */
  advanceNight: async (request: SpeechRequest): Promise<GameState> => {
    const response = await apiClient.post('/api/game/advance-night', request);
    return response.data;
  },

  /**
   * Submit human player speech
   * POST /api/game/human-speak
   */
  humanSpeak: async (request: { game_id: string; player_id: string; content: string }): Promise<ActionResult> => {
    const response = await apiClient.post('/api/game/human-speak', request);
    return response.data;
  },

  /**
   * Pre-discussion: generate AI speeches before human's turn
   * POST /api/game/pre-discussion
   */
  preDiscussion: async (request: SpeechRequest): Promise<GameState> => {
    const response = await apiClient.post('/api/game/pre-discussion', request);
    return response.data;
  },

  /**
   * Finish discussion: generate remaining AI speeches after human, transition to voting
   * POST /api/game/finish-discussion
   */
  finishDiscussion: async (request: SpeechRequest): Promise<GameState> => {
    const response = await apiClient.post('/api/game/finish-discussion', request);
    return response.data;
  },

  /**
   * Run full discussion (legacy, one-shot)
   * POST /api/game/run-discussion
   */
  runDiscussion: async (request: SpeechRequest): Promise<GameState> => {
    const response = await apiClient.post('/api/game/run-discussion', request);
    return response.data;
  },

  /**
   * End current round
   * POST /api/game/end-round
   */
  endRound: async (request: SpeechRequest): Promise<ActionResult> => {
    const response = await apiClient.post('/api/game/end-round', request);
    return response.data;
  },
};
