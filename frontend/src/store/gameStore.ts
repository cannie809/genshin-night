import { create } from 'zustand'
import type { GameState } from '../types/game'

export interface StickerBubble {
  playerName: string
  stickerUrl: string
  id: number
}

let _stickerIdCounter = 0

// --- Identity bootstrapping ---
// Client-generated UUID persisted to localStorage. The backend trusts
// whatever identity the client sends (anonymous multi-player), so this is
// NOT secret — it just needs to be stable across reloads so ack bookkeeping
// and player slot lookup keep pointing at the same human.
function getOrCreateIdentity(): string {
  const existing = localStorage.getItem('guest_identity')
  if (existing) return existing
  // crypto.randomUUID is widely supported in modern browsers. Fallback is
  // intentionally not silent — if this code runs somewhere without it we
  // want a clear error instead of a reused identity.
  const uuid = crypto.randomUUID()
  localStorage.setItem('guest_identity', uuid)
  return uuid
}

/**
 * Default display name when the user hasn't picked one. Derived from the
 * stable identity UUID (first 4 hex chars) so it's unique per browser and
 * matches the "Traveler_XXXX" convention the user asked for.
 */
function defaultDisplayName(identity: string): string {
  const suffix = identity.replace(/-/g, '').slice(0, 4)
  return `Traveler_${suffix}`
}

interface GameStore {
  // Current game state (matches backend response)
  gameState: GameState | null

  // Current player ID (slot id in spawned game, e.g. "player_3")
  playerId: string

  // Whether all events in GameLog have been revealed (stagger animation done)
  allEventsRevealed: boolean

  // Player role guesses (note-taking for human player)
  playerGuesses: Record<string, string>

  // Phase transition trigger (night/day overlay)
  phaseTransition: 'night' | 'day' | null

  // Active sticker bubbles displayed on PlayerCards
  activeStickerBubbles: StickerBubble[]

  // --- Identity / nickname / room routing ---
  // Stable client UUID (persisted). Used as Bearer token.
  identity: string
  // User-chosen nickname, max 16 chars. Null = NicknameGate must render.
  displayName: string | null
  // Which room the user is currently viewing/playing in. Null = RoomLobby.
  currentRoomId: string | null

  // Last game config (persists across games for "play again" in single-player)
  lastMode: string
  lastPreferredRole: string | null

  // Actions
  setDisplayName: (name: string | null) => void
  setCurrentRoomId: (id: string | null) => void
  setGameState: (state: GameState | null) => void
  setPlayerId: (playerId: string) => void
  setAllEventsRevealed: (val: boolean) => void
  setPlayerGuess: (playerId: string, role: string | null) => void
  setPhaseTransition: (type: 'night' | 'day' | null) => void
  addStickerBubble: (playerName: string, stickerUrl: string) => number
  removeStickerBubble: (id: number) => void
  setLastGameConfig: (mode: string, role: string | null) => void
  reset: () => void
}

// Bootstrap identity + displayName at module init so the defaulted
// displayName is derived from the final identity value.
const _identity = getOrCreateIdentity()
let _displayName = localStorage.getItem('guest_display_name')
// Migrate old auto-generated Guest_XXXX nicknames to Traveler_XXXX.
// We only rewrite the narrow `Guest_<4 hex>` auto-generated form — a user
// who manually typed "Guest_ABCD" as their nickname would also hit this,
// which we accept as a best-effort cleanup.
if (_displayName && /^Guest_[0-9a-f]{4}$/i.test(_displayName)) {
  _displayName = _displayName.replace(/^Guest_/i, 'Traveler_')
  localStorage.setItem('guest_display_name', _displayName)
}
if (!_displayName) {
  _displayName = defaultDisplayName(_identity)
  localStorage.setItem('guest_display_name', _displayName)
}

export const useGameStore = create<GameStore>((set) => ({
  gameState: null,
  playerId: 'player_0',
  allEventsRevealed: true,
  playerGuesses: {},
  phaseTransition: null,
  activeStickerBubbles: [],
  identity: _identity,
  displayName: _displayName,
  currentRoomId: null,
  lastMode: 'classic_6_witch',
  lastPreferredRole: null,

  setDisplayName: (name) => {
    if (name) {
      localStorage.setItem('guest_display_name', name)
    } else {
      localStorage.removeItem('guest_display_name')
    }
    set({ displayName: name })
  },

  setCurrentRoomId: (currentRoomId) => set({ currentRoomId }),

  setGameState: (gameState) => set({ gameState }),

  setPlayerId: (playerId) => set({ playerId }),

  setAllEventsRevealed: (allEventsRevealed) => set({ allEventsRevealed }),

  setPlayerGuess: (playerId, role) =>
    set((state) => {
      const next = { ...state.playerGuesses }
      if (role) {
        next[playerId] = role
      } else {
        delete next[playerId]
      }
      return { playerGuesses: next }
    }),

  setPhaseTransition: (phaseTransition) => set({ phaseTransition }),

  addStickerBubble: (playerName, stickerUrl) => {
    const id = ++_stickerIdCounter
    set((state) => ({
      activeStickerBubbles: [...state.activeStickerBubbles, { playerName, stickerUrl, id }],
    }))
    return id
  },

  removeStickerBubble: (id) =>
    set((state) => ({
      activeStickerBubbles: state.activeStickerBubbles.filter((b) => b.id !== id),
    })),

  setLastGameConfig: (mode, role) => set({ lastMode: mode, lastPreferredRole: role }),

  // Reset game state but preserve identity/displayName/room/config
  reset: () =>
    set((state) => ({
      gameState: null,
      playerId: 'player_0',
      allEventsRevealed: true,
      playerGuesses: {},
      phaseTransition: null,
      activeStickerBubbles: [],
      lastMode: state.lastMode,
      lastPreferredRole: state.lastPreferredRole,
    })),
}))
