"""Dynamic sticker registry — scans filesystem for character sticker PNGs."""

from functools import lru_cache
from pathlib import Path

# Sticker directory: frontend/public/stickers/{character_id}/*.png
_STICKER_ROOT = Path(__file__).resolve().parent.parent.parent / "frontend" / "public" / "stickers"


@lru_cache(maxsize=32)
def get_sticker_list(character_id: str) -> list[str]:
    """Return sorted list of sticker names (without .png extension) for a character.

    Args:
        character_id: Character personality ID (e.g. "hutao", "kazuha")

    Returns:
        List of sticker names, e.g. ["暗中窃喜", "有蹊跷", ...]
    """
    folder = _STICKER_ROOT / character_id
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.png"))


def get_sticker_url(character_id: str, name: str) -> str | None:
    """Validate a sticker name and return its URL path.

    Args:
        character_id: Character personality ID
        name: Sticker name (without extension)

    Returns:
        URL path like "/stickers/hutao/暗中窃喜.png", or None if invalid
    """
    if name in get_sticker_list(character_id):
        return f"/stickers/{character_id}/{name}.png"
    return None


def invalidate_cache() -> None:
    """Clear the sticker list cache (e.g. after adding new sticker files)."""
    get_sticker_list.cache_clear()
