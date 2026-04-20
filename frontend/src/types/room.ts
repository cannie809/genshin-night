// Frontend types for room/lobby endpoints. Mirrors RoomView in
// backend/room/router.py.

// Backend caps humans per room at 2 (see room manager). Empty seats are
// filled with AI on game start, so this is NOT the mode's total slot count.
export const MAX_HUMANS = 2

export type RoomStatus = 'waiting' | 'starting' | 'in_progress' | 'finished'

export interface RoomPlayer {
  identity: string
  display_name: string
  is_host: boolean
  // Only populated when player == the viewer. Backend scrubs for everyone
  // else so preferred_role never leaks strategic intel.
  preferred_role: string | null
  // Public ready flag (visible to everyone). Host changing mode clears
  // every non-host's ready on the server.
  ready: boolean
}

export interface RoomView {
  id: string // "room_0" ... "room_7"
  name: string // "房间 1" ... "房间 8"
  status: RoomStatus
  mode: string
  host_identity: string | null
  players: RoomPlayer[]
  game_id: string | null
  viewer_identity: string
  viewer_in_room: boolean
  // e.g. ["werewolf", "seer", "villager"] — render exactly these as the
  // 3-button role picker. Backend already filters by mode.
  role_options: string[]
}
