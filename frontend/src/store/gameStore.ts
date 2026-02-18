import { create } from 'zustand';
import type { GameState } from '../types/game';

export interface StickerBubble {
  playerName: string;
  stickerUrl: string;
  id: number;
}

let _stickerIdCounter = 0;

interface GameStore {
  // Current game state (matches backend response)
  gameState: GameState | null;

  // Current player ID (for human player)
  playerId: string;

  // Whether all events in GameLog have been revealed (stagger animation done)
  allEventsRevealed: boolean;

  // Player role guesses (note-taking for human player)
  playerGuesses: Record<string, string>;

  // Phase transition trigger (night/day overlay)
  phaseTransition: 'night' | 'day' | null;

  // Active sticker bubbles displayed on PlayerCards
  activeStickerBubbles: StickerBubble[];

  // Access key authentication
  accessKey: string | null;
  playerName: string | null;

  // Last game config (persists across games for "play again")
  lastMode: string;
  lastPreferredRole: string | null;

  // Actions
  setAccessKey: (key: string | null) => void;
  setPlayerName: (name: string | null) => void;
  setGameState: (state: GameState) => void;
  setPlayerId: (playerId: string) => void;
  setAllEventsRevealed: (val: boolean) => void;
  setPlayerGuess: (playerId: string, role: string | null) => void;
  setPhaseTransition: (type: 'night' | 'day' | null) => void;
  addStickerBubble: (playerName: string, stickerUrl: string) => number;
  removeStickerBubble: (id: number) => void;
  setLastGameConfig: (mode: string, role: string | null) => void;
  reset: () => void;
}

export const useGameStore = create<GameStore>((set) => ({
  gameState: null,
  playerId: 'player_0',
  allEventsRevealed: true,
  playerGuesses: {},
  phaseTransition: null,
  activeStickerBubbles: [],
  accessKey: localStorage.getItem('genshin_access_key'),
  playerName: null,
  lastMode: 'classic_6_witch',
  lastPreferredRole: null,

  setAccessKey: (key) => {
    if (key) {
      localStorage.setItem('genshin_access_key', key);
    } else {
      localStorage.removeItem('genshin_access_key');
    }
    set({ accessKey: key });
  },
  setPlayerName: (playerName) => set({ playerName }),

  setGameState: (gameState) => set({ gameState }),

  setPlayerId: (playerId) => set({ playerId }),

  setAllEventsRevealed: (allEventsRevealed) => set({ allEventsRevealed }),

  setPlayerGuess: (playerId, role) =>
    set((state) => {
      const next = { ...state.playerGuesses };
      if (role) {
        next[playerId] = role;
      } else {
        delete next[playerId];
      }
      return { playerGuesses: next };
    }),

  setPhaseTransition: (phaseTransition) => set({ phaseTransition }),

  addStickerBubble: (playerName, stickerUrl) => {
    const id = ++_stickerIdCounter;
    set((state) => ({
      activeStickerBubbles: [...state.activeStickerBubbles, { playerName, stickerUrl, id }],
    }));
    return id;
  },

  removeStickerBubble: (id) =>
    set((state) => ({
      activeStickerBubbles: state.activeStickerBubbles.filter((b) => b.id !== id),
    })),

  setLastGameConfig: (mode, role) => set({ lastMode: mode, lastPreferredRole: role }),

  // Reset game state but preserve lastMode/lastPreferredRole
  reset: () => set((state) => ({
    gameState: null, playerId: 'player_0', allEventsRevealed: true,
    playerGuesses: {}, phaseTransition: null, activeStickerBubbles: [],
    lastMode: state.lastMode, lastPreferredRole: state.lastPreferredRole,
  })),
}));
