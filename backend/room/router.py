"""HTTP router for room/lobby endpoints.

The game engine is kept out of this module — the manager receives a
`game_factory` closure at start-time (see `wire_router` in app.py). That
keeps the room subsystem testable without spinning up a full game engine.
"""

from __future__ import annotations

import logging
from typing import Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.models import Room, RoomPlayer, RoomStatus

from .manager import RoomManager, _role_options_for_mode

log = logging.getLogger(__name__)


# ---- Request models ----


class JoinRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=16)


class SetRoleRequest(BaseModel):
    # None clears the selection; an explicit string sets it.
    preferred_role: str | None = None


class SetModeRequest(BaseModel):
    mode: str


class SetReadyRequest(BaseModel):
    ready: bool


# ---- Response helpers ----


class RoomPlayerView(BaseModel):
    """What each viewer sees for each player in the room.

    preferred_role is only populated when the player == the viewer.
    `ready` is always public — the point is for host to see who's ready.
    """

    identity: str
    display_name: str
    is_host: bool
    # None unless this player is the viewer. Crucially, this prevents
    # strategic intel leaks (no "Alice is picking werewolf, let me pick
    # something complementary").
    preferred_role: str | None = None
    ready: bool = False


class RoomView(BaseModel):
    id: str
    name: str
    status: str
    mode: str
    host_identity: str | None
    players: list[RoomPlayerView]
    game_id: str | None
    viewer_identity: str
    viewer_in_room: bool
    # Role picks that are legal in the current mode (UI reads this to render
    # the 3-button role picker). Returned by the router so the frontend
    # doesn't need to duplicate GAME_MODES knowledge.
    role_options: list[str]


def _room_to_view(room: Room, viewer_identity: str) -> RoomView:
    viewer_in = room.find_player(viewer_identity) is not None
    players = [
        RoomPlayerView(
            identity=p.identity,
            display_name=p.display_name,
            is_host=(p.identity == room.host_identity),
            preferred_role=(p.preferred_role if p.identity == viewer_identity else None),
            ready=p.ready,
        )
        for p in room.players
    ]
    return RoomView(
        id=room.id,
        name=room.name,
        status=room.status.value,
        mode=room.mode,
        host_identity=room.host_identity,
        players=players,
        game_id=room.game_id,
        viewer_identity=viewer_identity,
        viewer_in_room=viewer_in,
        role_options=_role_options_for_mode(room.mode),
    )


# ---- Router ----


def build_router(
    manager: RoomManager,
    game_factory: Callable[[str, list[dict]], str],
) -> APIRouter:
    """Construct the /api/rooms router.

    `game_factory(mode, human_slots) -> game_id`: given a mode key and a
    list of HumanSlot dicts, build a GameState and return its game_id. The
    rooms manager stays decoupled from the engine via this closure.
    """
    router = APIRouter(prefix="/api/rooms", tags=["rooms"])

    def _viewer(request: Request) -> str:
        identity = getattr(request.state, "identity", None)
        if not identity:
            raise HTTPException(status_code=401, detail={"code": "MISSING_IDENTITY"})
        return identity

    @router.get("")
    async def list_rooms(request: Request) -> dict:
        viewer = _viewer(request)
        rooms = manager.list_rooms()
        return {"rooms": [_room_to_view(r, viewer).model_dump() for r in rooms]}

    @router.get("/{room_id}")
    async def get_room(room_id: str, request: Request) -> dict:
        viewer = _viewer(request)
        room = manager.get_room(room_id)
        if room is None:
            raise HTTPException(status_code=404, detail={"code": "ROOM_NOT_FOUND"})
        return _room_to_view(room, viewer).model_dump()

    @router.post("/{room_id}/join")
    async def join_room(room_id: str, body: JoinRequest, request: Request) -> dict:
        viewer = _viewer(request)
        try:
            room = await manager.join_room(room_id, viewer, body.display_name)
        except ValueError as e:
            # Bubble up a structured code so the frontend can branch.
            msg = str(e)
            if "does not exist" in msg:
                raise HTTPException(status_code=404, detail={"code": "ROOM_NOT_FOUND"})
            if "full" in msg:
                raise HTTPException(status_code=409, detail={"code": "ROOM_FULL"})
            if "not accepting joins" in msg:
                raise HTTPException(status_code=409, detail={"code": "ROOM_IN_PROGRESS"})
            log.warning("[rooms] join_room unexpected error: %s", e)
            raise HTTPException(status_code=400, detail={"code": "JOIN_FAILED", "message": msg})
        return _room_to_view(room, viewer).model_dump()

    @router.post("/{room_id}/leave")
    async def leave_room(room_id: str, request: Request) -> dict:
        viewer = _viewer(request)
        try:
            room = await manager.leave_room(room_id, viewer)
        except ValueError as e:
            if "does not exist" in str(e):
                raise HTTPException(status_code=404, detail={"code": "ROOM_NOT_FOUND"})
            raise HTTPException(status_code=400, detail={"code": "LEAVE_FAILED", "message": str(e)})
        return _room_to_view(room, viewer).model_dump()

    @router.post("/{room_id}/set-role")
    async def set_role(room_id: str, body: SetRoleRequest, request: Request) -> dict:
        viewer = _viewer(request)
        try:
            room = await manager.set_preferred_role(room_id, viewer, body.preferred_role)
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"code": "SET_ROLE_FAILED", "message": str(e)})
        return _room_to_view(room, viewer).model_dump()

    @router.post("/{room_id}/set-ready")
    async def set_ready(room_id: str, body: SetReadyRequest, request: Request) -> dict:
        viewer = _viewer(request)
        try:
            room = await manager.set_ready(room_id, viewer, body.ready)
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"code": "SET_READY_FAILED", "message": str(e)})
        return _room_to_view(room, viewer).model_dump()

    @router.post("/{room_id}/set-mode")
    async def set_mode(room_id: str, body: SetModeRequest, request: Request) -> dict:
        viewer = _viewer(request)
        try:
            room = await manager.set_mode(room_id, viewer, body.mode)
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"code": "SET_MODE_FAILED", "message": str(e)})
        return _room_to_view(room, viewer).model_dump()

    @router.post("/{room_id}/start")
    async def start_game(room_id: str, request: Request) -> dict:
        viewer = _viewer(request)
        try:
            room = await manager.start_game(room_id, viewer, game_factory)
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"code": "START_FAILED", "message": str(e)})
        return _room_to_view(room, viewer).model_dump()

    @router.post("/{room_id}/restart")
    async def restart_room(room_id: str, request: Request) -> dict:
        viewer = _viewer(request)
        try:
            room = await manager.restart_room(room_id, viewer)
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"code": "RESTART_FAILED", "message": str(e)})
        return _room_to_view(room, viewer).model_dump()

    return router
