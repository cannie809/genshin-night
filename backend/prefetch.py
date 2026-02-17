"""Async prefetch manager for LLM call results.

Launches LLM calls during idle windows (between user clicks) and caches results
so endpoints can return near-instantly when the user eventually clicks.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Coroutine

log = logging.getLogger("werewolf")

CacheKey = tuple[str, str, int]  # (game_id, action_type, round_number)


@dataclass
class PrefetchEntry:
    """A single prefetch cache entry."""

    task: asyncio.Task
    created_at: float = field(default_factory=time.monotonic)


class PrefetchManager:
    """Simple async cache manager for prefetched LLM results.

    Usage:
        await prefetch.launch(game_id, "pre_speeches", round_num, coro)
        result = await prefetch.get_or_wait(game_id, "pre_speeches", round_num, timeout=0.1)
        if result is not None:
            # use cached result
        else:
            # fallback to synchronous logic
    """

    def __init__(self) -> None:
        self._cache: dict[CacheKey, PrefetchEntry] = {}

    async def launch(
        self,
        game_id: str,
        action: str,
        round_num: int,
        coro: Coroutine,
    ) -> None:
        """Launch a prefetch task. If one already exists for this key, skip."""
        key: CacheKey = (game_id, action, round_num)
        if key in self._cache:
            entry = self._cache[key]
            if not entry.task.done() or not entry.task.cancelled():
                log.info(f"[Prefetch] Already running: {action} round={round_num}")
                return
        task = asyncio.create_task(coro)
        self._cache[key] = PrefetchEntry(task=task)
        log.info(f"[Prefetch] Launched: {action} round={round_num}")

    async def get_or_wait(
        self,
        game_id: str,
        action: str,
        round_num: int,
        timeout: float = 0.1,
    ) -> Any | None:
        """Get a prefetched result, waiting up to `timeout` seconds.

        Returns None on cache miss, timeout, or if the prefetch task failed.
        Callers should fall back to synchronous logic when None is returned.
        """
        key: CacheKey = (game_id, action, round_num)
        entry = self._cache.get(key)
        if entry is None:
            log.info(f"[Prefetch] Cache miss: {action} round={round_num}")
            return None
        try:
            result = await asyncio.wait_for(asyncio.shield(entry.task), timeout=timeout)
            elapsed = time.monotonic() - entry.created_at
            log.info(f"[Prefetch] Cache hit: {action} round={round_num} ({elapsed:.1f}s prefetch time)")
            # Consume the entry
            del self._cache[key]
            return result
        except asyncio.TimeoutError:
            log.info(f"[Prefetch] Timeout ({timeout}s): {action} round={round_num}, falling back")
            return None
        except (Exception, asyncio.CancelledError) as e:
            log.warning(f"[Prefetch] Task failed: {action} round={round_num}: {e}")
            self._cache.pop(key, None)
            return None

    def invalidate(self, game_id: str) -> None:
        """Cancel and remove all prefetch tasks for a game."""
        to_remove = [k for k in self._cache if k[0] == game_id]
        for key in to_remove:
            entry = self._cache.pop(key)
            if not entry.task.done():
                entry.task.cancel()
        if to_remove:
            log.info(f"[Prefetch] Invalidated {len(to_remove)} entries for game {game_id[:8]}...")
