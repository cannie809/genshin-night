"""RoomManager — in-process registry of pre-allocated rooms.

Concurrency model: each room has its own asyncio.Lock. All mutations
(join/leave/set_role/start/restart) acquire the per-room lock. Cross-room
operations (listing, finding the room that owns a given identity) do not
require any lock — they only read atomic snapshots.

This design assumes a single-process deployment. If the backend is ever
sharded, rooms need to move to a shared store (Redis/DB) with distributed
locks; the manager's surface stays the same.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from backend.config import DEFAULT_MODE, GAME_MODES
from backend.models import Room, RoomPlayer, RoomStatus

log = logging.getLogger(__name__)


DEFAULT_ROOM_COUNT = 8
# Hard cap on human participants per room. The game itself is a 6-seat
# classic_6 mode, but the multi-human gameplay loop is designed for at
# most 2 humans; the other 4 seats are always AI.
MAX_HUMANS_PER_ROOM = 2


def _role_options_for_mode(mode: str) -> list[str]:
    """Return the picking-legal preferred roles for a mode.

    Expose every distinct role present in the mode's role list so the UI
    can render a full picker (werewolf/seer/witch/hunter/guard/villager).
    Order matches a stable display sequence for the frontend.
    """
    if mode not in GAME_MODES:
        return []
    mode_roles = set(GAME_MODES[mode].roles)
    display_order = ["werewolf", "seer", "witch", "hunter", "guard", "villager"]
    return [r for r in display_order if r in mode_roles]


class RoomManager:
    """Fixed-size room pool with per-room locking.

    Rooms are created once at construction and never deleted — join/leave
    just mutate membership. A room may be "empty" (no players) but still
    exists in the pool as an available slot for new joins.
    """

    def __init__(self, room_count: int = DEFAULT_ROOM_COUNT, default_mode: str = DEFAULT_MODE):
        self._rooms: dict[str, Room] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # Reverse index so a game_id → room_id lookup is O(1). Populated on
        # start_game and cleared on restart_room. Used by the victory hook
        # to flip the owning room to FINISHED without threading room_id
        # through every endpoint.
        self._game_to_room: dict[str, str] = {}
        for i in range(room_count):
            room_id = f"room_{i}"
            self._rooms[room_id] = Room(
                id=room_id,
                name=f"房间 {i + 1}",
                mode=default_mode,
            )
            self._locks[room_id] = asyncio.Lock()
        log.info("[RoomManager] initialized with %d rooms (mode=%s)", room_count, default_mode)

    def room_id_for_game(self, game_id: str) -> str | None:
        """Return the room_id that owns this game_id, or None."""
        return self._game_to_room.get(game_id)

    # ---- Read ----

    def list_rooms(self) -> list[Room]:
        """Snapshot all rooms. No lock — Pydantic models are copy-on-write
        via `.model_copy()`, so callers can serialize safely."""
        return [r.model_copy(deep=True) for r in self._rooms.values()]

    def get_room(self, room_id: str) -> Room | None:
        room = self._rooms.get(room_id)
        return room.model_copy(deep=True) if room else None

    def find_room_for_identity(self, identity: str) -> Room | None:
        """Return the (first) room whose player list contains the identity."""
        for r in self._rooms.values():
            if r.find_player(identity):
                return r.model_copy(deep=True)
        return None

    # ---- Mutations (all locked) ----

    async def join_room(self, room_id: str, identity: str, display_name: str) -> Room:
        """Add this identity to the room.

        If the identity is already in the room, we refresh their display name
        and return (idempotent). If the room is not WAITING, raise. If the
        room is full, raise.

        Full = len(players) >= number of roles in the selected mode (we can't
        seat more humans than slots).
        """
        lock = self._locks[room_id]
        async with lock:
            room = self._rooms.get(room_id)
            if room is None:
                raise ValueError(f"room {room_id!r} does not exist")
            # FINISHED rooms are treated as "restartable" — a fresh joiner
            # triggers an implicit reset so the room is usable again without
            # requiring the previous host to click 再来一局 first. IN_PROGRESS
            # / STARTING rooms are still closed to new joiners.
            if room.status == RoomStatus.FINISHED:
                log.info(
                    "[RoomManager] join into FINISHED %s; auto-resetting to WAITING",
                    room_id,
                )
                if room.game_id:
                    self._game_to_room.pop(room.game_id, None)
                room.game_id = None
                room.status = RoomStatus.WAITING
                room.started_at = None
                for p in room.players:
                    p.ready = False
            if room.status != RoomStatus.WAITING:
                raise ValueError(f"room {room_id} is not accepting joins (status={room.status.value})")

            existing = room.find_player(identity)
            if existing:
                # Refresh nickname (user might have renamed).
                existing.display_name = display_name
                log.info(
                    "[RoomManager] %s rejoined %s (display_name=%r)",
                    identity[:8],
                    room_id,
                    display_name,
                )
                return room.model_copy(deep=True)

            if len(room.players) >= MAX_HUMANS_PER_ROOM:
                raise ValueError(
                    f"room {room_id} is full ({len(room.players)}/{MAX_HUMANS_PER_ROOM})"
                )

            room.players.append(RoomPlayer(identity=identity, display_name=display_name))
            if room.host_identity is None:
                room.host_identity = identity
                log.info("[RoomManager] %s became host of %s", identity[:8], room_id)
            log.info(
                "[RoomManager] %s joined %s (%d/%d)",
                identity[:8],
                room_id,
                len(room.players),
                MAX_HUMANS_PER_ROOM,
            )
            return room.model_copy(deep=True)

    async def leave_room(self, room_id: str, identity: str) -> Room:
        """Remove this identity from the room. If they were host, pass the
        host badge to the next-earliest joiner. Empty rooms stay in the pool
        in WAITING state. Leaving an IN_PROGRESS room doesn't cancel the
        game — it only removes the user from the room roster."""
        lock = self._locks[room_id]
        async with lock:
            room = self._rooms.get(room_id)
            if room is None:
                raise ValueError(f"room {room_id!r} does not exist")
            player = room.find_player(identity)
            if not player:
                # Idempotent — leaving a room you're not in is a no-op but we
                # log because the frontend shouldn't normally trigger this.
                log.warning(
                    "[RoomManager] leave_room: %s not in %s; returning current state",
                    identity[:8],
                    room_id,
                )
                return room.model_copy(deep=True)
            room.players = [p for p in room.players if p.identity != identity]
            if room.host_identity == identity:
                room.host_identity = room.players[0].identity if room.players else None
                if room.host_identity:
                    log.info("[RoomManager] host transferred to %s in %s", room.host_identity[:8], room_id)
            # An empty room snaps back to WAITING and releases any lingering
            # game handle. Without this, a room that finished a game and
            # then had everyone leave would sit in FINISHED forever, and
            # the lobby would show it as "已结束" instead of "空房间".
            if not room.players:
                if room.game_id:
                    self._game_to_room.pop(room.game_id, None)
                room.game_id = None
                room.status = RoomStatus.WAITING
                room.started_at = None
            log.info("[RoomManager] %s left %s (remaining=%d)", identity[:8], room_id, len(room.players))
            return room.model_copy(deep=True)

    async def set_preferred_role(
        self, room_id: str, identity: str, preferred_role: str | None
    ) -> Room:
        """Set this identity's preferred role. Each player picks independently;
        conflicts are resolved at `start_game` time (list-order priority).
        Refuses when the player has already flagged themselves ready — they
        must cancel ready first."""
        lock = self._locks[room_id]
        async with lock:
            room = self._rooms.get(room_id)
            if room is None:
                raise ValueError(f"room {room_id!r} does not exist")
            player = room.find_player(identity)
            if not player:
                raise ValueError(f"identity {identity!r} not in room {room_id}")
            if room.status != RoomStatus.WAITING:
                raise ValueError(f"room {room_id} is not in waiting state")
            if player.ready:
                raise ValueError(
                    "cannot change role while ready — cancel ready (取消准备) first"
                )
            allowed = _role_options_for_mode(room.mode)
            if preferred_role is not None and preferred_role not in allowed:
                raise ValueError(
                    f"role {preferred_role!r} not selectable for mode {room.mode} (allowed: {allowed})"
                )
            player.preferred_role = preferred_role
            log.info(
                "[RoomManager] %s set preferred_role=%s in %s",
                identity[:8],
                preferred_role,
                room_id,
            )
            return room.model_copy(deep=True)

    async def set_ready(
        self, room_id: str, identity: str, ready: bool
    ) -> Room:
        """Toggle this identity's ready flag. Advisory only — host sees the
        aggregate ready count but isn't blocked from starting. Hosts can
        flip their own flag too; it's harmless."""
        lock = self._locks[room_id]
        async with lock:
            room = self._rooms.get(room_id)
            if room is None:
                raise ValueError(f"room {room_id!r} does not exist")
            player = room.find_player(identity)
            if not player:
                raise ValueError(f"identity {identity!r} not in room {room_id}")
            if room.status != RoomStatus.WAITING:
                raise ValueError(f"room {room_id} is not in waiting state")
            player.ready = ready
            log.info(
                "[RoomManager] %s set ready=%s in %s",
                identity[:8],
                ready,
                room_id,
            )
            return room.model_copy(deep=True)

    async def set_mode(self, room_id: str, identity: str, mode: str) -> Room:
        """Host-only: change the room's game mode while waiting."""
        lock = self._locks[room_id]
        async with lock:
            room = self._rooms.get(room_id)
            if room is None:
                raise ValueError(f"room {room_id!r} does not exist")
            if not room.is_host(identity):
                raise ValueError("only host can change mode")
            if room.status != RoomStatus.WAITING:
                raise ValueError(f"room {room_id} is not in waiting state")
            if mode not in GAME_MODES:
                raise ValueError(f"invalid mode {mode!r}")
            room.mode = mode
            # Clear preferred_role for anyone whose pick is no longer valid.
            allowed = set(_role_options_for_mode(mode))
            for p in room.players:
                if p.preferred_role is not None and p.preferred_role not in allowed:
                    log.warning(
                        "[RoomManager] mode change to %s invalidates %s's preferred_role=%s; clearing",
                        mode,
                        p.identity[:8],
                        p.preferred_role,
                    )
                    p.preferred_role = None
            # Mode change invalidates every player's "ready" — they need to
            # re-confirm with the new mode. Host included.
            for p in room.players:
                if p.ready:
                    log.info(
                        "[RoomManager] mode change clears ready for %s",
                        p.identity[:8],
                    )
                p.ready = False
            log.info("[RoomManager] %s set mode=%s in %s", identity[:8], mode, room_id)
            return room.model_copy(deep=True)

    async def start_game(
        self,
        room_id: str,
        identity: str,
        game_factory: Callable[[str, list[dict]], str],
    ) -> Room:
        """Host-only: transition WAITING → STARTING → IN_PROGRESS.

        `game_factory(mode, human_slots) -> game_id` is injected so the
        manager stays independent of the game engine. Under the lock we:
          1. Flip status to STARTING so concurrent /start calls see the guard.
          2. Call factory; on success, record game_id and flip to IN_PROGRESS.
          3. On factory failure, roll back to WAITING and re-raise.
        """
        lock = self._locks[room_id]
        async with lock:
            room = self._rooms.get(room_id)
            if room is None:
                raise ValueError(f"room {room_id!r} does not exist")
            if not room.is_host(identity):
                raise ValueError("only host can start the game")
            if room.status != RoomStatus.WAITING:
                raise ValueError(f"room {room_id} is not in waiting state (status={room.status.value})")
            if not room.players:
                raise ValueError(f"room {room_id} has no players to start with")
            if room.mode not in GAME_MODES:
                raise ValueError(f"invalid mode {room.mode!r}")
            if len(room.players) > MAX_HUMANS_PER_ROOM:
                raise ValueError(
                    f"more humans ({len(room.players)}) than max ({MAX_HUMANS_PER_ROOM})"
                )
            # Block start until every non-host member has clicked 准备. A
            # solo host (single-player) has no non-host members and passes
            # trivially — keeps single-player non-degraded.
            unready = [
                p for p in room.players
                if p.identity != room.host_identity and not p.ready
            ]
            if unready:
                raise ValueError(
                    f"not all players are ready: {len(unready)}/{len(room.players) - 1} non-host still not ready"
                )

            room.status = RoomStatus.STARTING
            human_slots = [
                {
                    "identity": p.identity,
                    "display_name": p.display_name,
                    "preferred_role": p.preferred_role,
                }
                for p in room.players
            ]
            try:
                game_id = game_factory(room.mode, human_slots)
            except Exception as e:
                log.error("[RoomManager] game_factory failed for %s: %s", room_id, e)
                room.status = RoomStatus.WAITING
                raise
            room.game_id = game_id
            room.status = RoomStatus.IN_PROGRESS
            self._game_to_room[game_id] = room_id
            from time import time as _now

            room.started_at = _now()
            log.info(
                "[RoomManager] %s started game %s in %s (humans=%d mode=%s)",
                identity[:8],
                game_id[:8] if game_id else "?",
                room_id,
                len(room.players),
                room.mode,
            )
            return room.model_copy(deep=True)

    async def mark_game_finished(self, room_id: str) -> Room | None:
        """Called by the game engine (or an endpoint) when the GameState
        reaches GAME_END. Flips the room to FINISHED so the host can restart.
        Safe to call redundantly."""
        lock = self._locks[room_id]
        async with lock:
            room = self._rooms.get(room_id)
            if room is None:
                return None
            if room.status == RoomStatus.IN_PROGRESS:
                room.status = RoomStatus.FINISHED
                log.info("[RoomManager] %s marked FINISHED", room_id)
            return room.model_copy(deep=True)

    async def restart_room(self, room_id: str, identity: str) -> Room:
        """Host-only: clear game_id and return to WAITING so the room can
        start another game with the same members. Preferred roles are kept
        so players don't have to re-pick."""
        lock = self._locks[room_id]
        async with lock:
            room = self._rooms.get(room_id)
            if room is None:
                raise ValueError(f"room {room_id!r} does not exist")
            if not room.is_host(identity):
                raise ValueError("only host can restart the room")
            if room.status not in (RoomStatus.FINISHED, RoomStatus.IN_PROGRESS):
                raise ValueError(
                    f"room {room_id} has no finished game to restart (status={room.status.value})"
                )
            old_game_id = room.game_id
            if old_game_id:
                self._game_to_room.pop(old_game_id, None)
            room.game_id = None
            room.status = RoomStatus.WAITING
            room.started_at = None
            # Reset ready flags so players have to re-confirm for the next game.
            for p in room.players:
                p.ready = False
            log.info(
                "[RoomManager] %s restarted %s (old_game=%s)",
                identity[:8],
                room_id,
                old_game_id[:8] if old_game_id else "?",
            )
            return room.model_copy(deep=True)


_singleton: RoomManager | None = None


def get_room_manager() -> RoomManager:
    """Lazy singleton accessor. The first caller seeds the pool."""
    global _singleton
    if _singleton is None:
        _singleton = RoomManager()
    return _singleton
