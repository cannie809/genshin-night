"""Event index manager - maintains structured index of key game facts.

This module provides EventIndexManager which persists critical facts even after
day_record slides out of its 3-round window. Zero LLM cost - pure program logic.

The index tracks:
- Role claims (verified, contested, disproven)
- Deaths (night kills, votes, poison, hunter)
- Key contradictions between players
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class EventIndexManager:
    """Maintains a structured event index for persistent key facts.

    This index survives the day_record sliding window (3 rounds),
    ensuring critical game facts are never lost.
    Zero LLM cost - pure program logic.
    """

    def __init__(self, memory_base: Path | str, game_id: str):
        """Initialize event index manager.

        Args:
            memory_base: Base directory for all memory files
            game_id: Game ID to isolate memories per game
        """
        self.index_path = Path(memory_base) / game_id / "event_index.json"

    def initialize(self) -> None:
        """Create empty event index."""
        self.index_path.parent.mkdir(parents=True, exist_ok=True)

        initial_data = {"claims": [], "deaths": [], "key_contradictions": []}

        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(initial_data, f, indent=2, ensure_ascii=False)

        log.info(f"Initialized event index at {self.index_path}")

    def record_death(
        self,
        round_num: int,
        player_name: str,
        cause: str,
        is_night: bool = True,
    ) -> None:
        """Record a player death.

        Args:
            round_num: Round number
            player_name: Name of dead player
            cause: One of "werewolf_kill", "witch_poison", "vote_elimination", "hunter_shot"
            is_night: Whether death occurred at night
        """
        data = self._read_index()

        death_record = {
            "round": round_num,
            "night": is_night,
            "player": player_name,
            "cause": cause,
        }

        # Check for duplicates
        for existing in data["deaths"]:
            if existing["round"] == round_num and existing["player"] == player_name and existing["night"] == is_night:
                log.warning(f"Death already recorded: {player_name} in round {round_num}")
                return

        data["deaths"].append(death_record)
        self._write_index(data)

        log.info(f"Recorded death: {player_name} ({cause}) in round {round_num}, night={is_night}")

    def record_claim(self, round_num: int, player_name: str, claimed_role: str, status: str = "unverified") -> None:
        """Record a role claim.

        Args:
            round_num: Round number
            player_name: Name of player making claim
            claimed_role: Role being claimed (e.g., "seer", "witch")
            status: "unverified", "verified", "contested", "disproven"
        """
        data = self._read_index()

        claim_record = {"round": round_num, "player": player_name, "claimed_role": claimed_role, "status": status}

        # Check if player already has a claim - update instead
        for existing in data["claims"]:
            if existing["player"] == player_name:
                log.info(f"Updating existing claim for {player_name}")
                existing["round"] = round_num
                existing["claimed_role"] = claimed_role
                existing["status"] = status
                self._write_index(data)
                return

        data["claims"].append(claim_record)
        self._write_index(data)

        log.info(f"Recorded claim: {player_name} claims {claimed_role} (round {round_num}, {status})")

    def update_claim_status(self, player_name: str, new_status: str) -> None:
        """Update the status of a player's role claim.

        Args:
            player_name: Name of player
            new_status: New status ("verified", "contested", "disproven")
        """
        data = self._read_index()

        found = False
        for claim in data["claims"]:
            if claim["player"] == player_name:
                old_status = claim["status"]
                claim["status"] = new_status
                found = True
                log.info(f"Updated claim status for {player_name}: {old_status} -> {new_status}")
                break

        if not found:
            log.warning(f"No claim found for {player_name}, cannot update status")
            return

        self._write_index(data)

    def record_contradiction(self, round_num: int, players: list[str], contradiction_type: str) -> None:
        """Record a contradiction between players.

        Args:
            round_num: Round number
            players: List of player names involved
            contradiction_type: e.g. "seer_conflict", "claim_conflict", "alibi_conflict"
        """
        data = self._read_index()

        contradiction_record = {"round": round_num, "between": players, "type": contradiction_type}

        # Check for duplicates
        for existing in data["key_contradictions"]:
            if (
                existing["round"] == round_num
                and set(existing["between"]) == set(players)
                and existing["type"] == contradiction_type
            ):
                log.warning(f"Contradiction already recorded: {contradiction_type} in round {round_num}")
                return

        data["key_contradictions"].append(contradiction_record)
        self._write_index(data)

        log.info(f"Recorded contradiction: {contradiction_type} between {players} in round {round_num}")

    def get_key_facts(self, viewer_role: str | None = None) -> str:
        """Format key facts for prompt injection.

        Returns a human-readable summary of all indexed events.
        Format in Chinese for prompt compatibility.

        Args:
            viewer_role: Optional role filter (e.g., some facts may be role-specific)

        Returns:
            Formatted string for prompt injection, or empty string if no facts
        """
        if not self.index_path.exists():
            return ""

        data = self._read_index()

        # Check if there are any facts
        if not data["deaths"] and not data["claims"] and not data["key_contradictions"]:
            return ""

        lines = ["### 关键事实"]

        # Format deaths
        if data["deaths"]:
            lines.append("**死亡记录**:")
            for death in data["deaths"]:
                time_label = "夜" if death["night"] else "日"
                cause_map = {
                    "werewolf_kill": "被狼人击杀",
                    "witch_poison": "被女巫毒杀",
                    "vote_elimination": "被投票出局",
                    "hunter_shot": "被猎人击杀",
                }
                cause_text = cause_map.get(death["cause"], death["cause"])
                lines.append(f"- 第{death['round']}轮({time_label}): {death['player']} {cause_text}")
                # Show hunter identity after hunter_shot death
                if death["cause"] == "hunter_shot":
                    for claim in data["claims"]:
                        if claim["claimed_role"] == "hunter" and claim["status"] == "revealed":
                            lines.append(f"- 猎人是{claim['player']}")
                            break

        # Format claims (exclude revealed identities — already shown above)
        regular_claims = [c for c in data["claims"] if c["status"] != "revealed"]
        if regular_claims:
            lines.append("\n**身份声明**:")
            for claim in regular_claims:
                status_map = {
                    "unverified": "未验证",
                    "verified": "已验证",
                    "contested": "有争议",
                    "disproven": "已证伪",
                }
                status_text = status_map.get(claim["status"], claim["status"])
                lines.append(f"- {claim['player']} 声称{claim['claimed_role']} (第{claim['round']}轮, {status_text})")

        # Format contradictions
        if data["key_contradictions"]:
            lines.append("\n**矛盾记录**:")
            for contradiction in data["key_contradictions"]:
                players_str = " vs ".join(contradiction["between"])
                type_map = {
                    "seer_conflict": "预言家冲突",
                    "claim_conflict": "身份声明冲突",
                    "alibi_conflict": "不在场证明冲突",
                }
                type_text = type_map.get(contradiction["type"], contradiction["type"])
                lines.append(f"- 第{contradiction['round']}轮: {players_str} — {type_text}")

        return "\n".join(lines)

    def _read_index(self) -> dict:
        """Read event index from disk."""
        if not self.index_path.exists():
            # Return empty structure if file doesn't exist
            return {"claims": [], "deaths": [], "key_contradictions": []}

        with open(self.index_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_index(self, data: dict) -> None:
        """Write event index to disk."""
        self.index_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
