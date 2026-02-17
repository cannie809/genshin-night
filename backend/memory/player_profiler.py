"""PlayerProfiler — Cross-game human player behavior profiling.

Learns human player (旅行者 / player_0) behavior patterns from completed games
and provides selective, role-aware observations to AI players via SQLite storage.

Storage layout:
    .memory/_cross_game/player_profile.db   # SQLite database
"""

import json
import logging
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# Keep this many most recent game directories
KEEP_RECENT_GAMES = 10

# Perspective matrix: (human_role_side) -> [teammate_perspective, opponent_perspective]
# Win/loss no longer splits perspective type — it's embedded in the prompt wording instead.
_PERSPECTIVE_MATRIX = {
    "wolf": ["wolf_teammate", "good_opponent"],   # 狼队友 + 好人对手
    "good": ["good_teammate", "wolf_opponent"],   # 好人队友 + 狼人对手
}

# Role to side mapping
_ROLE_SIDE = {
    "werewolf": "wolf",
    "seer": "good",
    "witch": "good",
    "hunter": "good",
    "villager": "good",
}

# Perspective to AI role relevance
_PERSPECTIVE_FOR_AI_ROLE = {
    "werewolf": ["wolf_teammate", "wolf_opponent"],
    "seer": ["good_teammate", "good_opponent"],
    "witch": ["good_teammate", "good_opponent"],
    "hunter": ["good_teammate", "good_opponent"],
    "villager": ["good_teammate", "good_opponent"],
}

_ROLE_CN = {
    "werewolf": "狼人", "seer": "预言家", "witch": "女巫",
    "hunter": "猎人", "villager": "村民",
}

_SIDE_CN = {"wolf": "狼人阵营", "good": "好人阵营"}


def _build_perspective_desc(
    perspective: str, human_role: str, human_won: bool,
) -> tuple[str, str]:
    """Build a context-specific perspective description for the distillation prompt.

    Only 4 perspective types. Win/loss is embedded in the wording, not the tag.

    Returns:
        (short_label, analysis_instruction)
    """
    role_cn = _ROLE_CN.get(human_role, human_role)
    human_side = _ROLE_SIDE.get(human_role, "good")
    human_side_cn = _SIDE_CN.get(human_side, human_side)
    outcome = "获胜" if human_won else "落败"

    if perspective == "wolf_teammate":
        label = "狼人队友"
        if human_won:
            instruction = (
                f"这局旅行者是{role_cn}（{human_side_cn}），最终{outcome}。"
                f"你也是狼人阵营的AI，你和旅行者是队友。"
                f"分析：旅行者的哪些策略帮助了狼队？下次你作为队友如何更好地配合这些打法？"
            )
        else:
            instruction = (
                f"这局旅行者是{role_cn}（{human_side_cn}），最终{outcome}。"
                f"你也是狼人阵营的AI，你和旅行者是队友。"
                f"每条格式：'当观察到旅行者XX时，狼队应YY'（如：分工扛推、悍跳时机、刀人优先级）。"
            )
    elif perspective == "good_teammate":
        label = "好人队友"
        if human_won:
            instruction = (
                f"这局旅行者是{role_cn}（{human_side_cn}），最终{outcome}。"
                f"你也是好人阵营的AI，你和旅行者是队友。"
                f"分析：旅行者展现了哪些行为习惯？（如发言风格、投票倾向、身份声明方式）"
                f"好人队友下次如何根据这些特点更好地辨认和配合旅行者？"
            )
        else:
            instruction = (
                f"这局旅行者是{role_cn}（{human_side_cn}），最终{outcome}。"
                f"你也是好人阵营的AI，你和旅行者是队友。"
                f"每条格式：'当观察到旅行者XX时，队友应YY'（如：主动先发言给素材、投票跟随形成合力）。"
            )
    elif perspective == "good_opponent":
        label = "好人对手"
        if human_won:
            instruction = (
                f"这局旅行者是{role_cn}（{human_side_cn}），最终{outcome}。"
                f"你是好人阵营的AI，旅行者是你的对手。"
                f"分析：旅行者的狼人策略中什么最有效？下次好人应该怎么对付或识破？"
            )
        else:
            instruction = (
                f"这局旅行者是{role_cn}（{human_side_cn}），最终{outcome}。"
                f"你是好人阵营的AI，旅行者是你的对手。"
                f"分析：旅行者的狼人打法暴露了什么弱点？下次好人如何利用这些弱点取胜？"
            )
    elif perspective == "wolf_opponent":
        label = "狼人对手"
        if human_won:
            instruction = (
                f"这局旅行者是{role_cn}（{human_side_cn}），最终{outcome}。"
                f"你是狼人阵营的AI，旅行者是你的对手。"
                f"分析：旅行者的好人策略中什么威胁最大？下次狼人应该怎么对付或绕过？"
            )
        else:
            instruction = (
                f"这局旅行者是{role_cn}（{human_side_cn}），最终{outcome}。"
                f"你是狼人阵营的AI，旅行者是你的对手。"
                f"分析：旅行者的好人打法暴露了什么弱点？下次狼人如何利用这些弱点取胜？"
            )
    else:
        return (perspective, perspective)

    return (label, instruction)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS player_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_role TEXT NOT NULL,
    game_phase TEXT NOT NULL,
    behavior_category TEXT NOT NULL,
    observation TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '',
    game_outcome TEXT NOT NULL,
    outcome_perspective TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    match_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_game_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_obs_role_phase
    ON player_observations(player_role, game_phase);
CREATE INDEX IF NOT EXISTS idx_obs_perspective
    ON player_observations(outcome_perspective);
CREATE INDEX IF NOT EXISTS idx_obs_confidence
    ON player_observations(confidence DESC);

CREATE TABLE IF NOT EXISTS game_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id TEXT NOT NULL UNIQUE,
    human_role TEXT NOT NULL,
    game_outcome TEXT NOT NULL,
    winning_side TEXT NOT NULL,
    distilled_at TEXT NOT NULL,
    observation_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS served_observations (
    game_id TEXT NOT NULL,
    observation_id INTEGER NOT NULL,
    PRIMARY KEY (game_id, observation_id)
);
"""

_DISTILL_PROMPT_TEMPLATE = """你是狼人杀行为分析师。分析以下游戏中"旅行者"（人类玩家）的行为模式。

## 游戏信息
- 旅行者的角色: {human_role_cn}
- 游戏结果: {outcome_text}
- 获胜方: {winning_side_cn}

## 旅行者的行动记录

### 发言记录
{speech_records}

### 投票记录
{vote_records}

### 夜间行动
{night_records}

## 分析任务

从以下视角分别提取行为模式（每个视角2-3条）：

{perspective_sections}

每条必须包含:
- perspective: 视角标签（必须是上述视角之一: {perspective_keys}）
- game_phase: "early"(第1轮) / "mid"(第2轮) / "late"(第3轮+)
- behavior_category: speech_pattern / voting_pattern / silence_pattern / accusation_pattern / defense_pattern / night_action / cooperation_pattern / deception_pattern / identity_claim 之一
- observation: 从该视角出发的行为分析(80-150字中文，须包含具体时机、话术方向或配合方式等可执行细节)
- keywords: 从下方词表中选3-5个关键词，逗号分隔

**关键词词表（必须从中选择，不要自创）**:
发言类: 沉默,试探,质疑,附和,带节奏,强势发言,模糊发言,逻辑分析,情感诉求,转移话题
投票类: 跟票,冲票,弃票,保护队友,抗推,分票,集火
身份类: 明跳,暗示身份,隐藏身份,假跳,对跳,验人,自证
策略类: 过早暴露,信息泄露,孤立无援,配合默契,战术失误,节奏把控,带队能力
防守类: 慌张辩解,逻辑自洽,情绪化,冷静应对,甩锅,认错

**关键要求**: 不同视角必须给出不同的分析角度。同一行为，配合方应关注"如何利用/配合"，对抗方应关注"如何识破/对付"。

不要提取:
- 常规操作（如"投了票"这样的纯事实）
- 一次性偶然决策
- 关于AI玩家的观察

只输出JSON数组，不要其他内容。示例:
[
  {{"perspective": "good_teammate", "game_phase": "early", "behavior_category": "speech_pattern", "observation": "当观察到旅行者首轮沉默观察不主动发言时，队友应在旅行者之前发言并抛出明确观点（如'我觉得3号有问题'），给旅行者提供判断素材和站边锚点，避免全场沉默导致狼人掌握节奏", "keywords": "沉默,试探,配合默契"}},
  {{"perspective": "wolf_opponent", "game_phase": "early", "behavior_category": "speech_pattern", "observation": "旅行者首轮沉默时正在收集各方发言信息，狼人应控制首轮发言量、避免给出过多细节，同时安排一名狼人主动带节奏将焦点引向其他好人，打乱旅行者的信息收集", "keywords": "沉默,隐藏身份,冷静应对"}}
]"""


class PlayerProfiler:
    """Cross-game human player behavior profiler.

    Learns behavior patterns of the human player (旅行者) from completed games
    and provides targeted observations to AI players based on role and phase.

    Args:
        memory_base: Path to .memory/ directory
    """

    def __init__(self, memory_base: Path | str):
        self.memory_base = Path(memory_base)
        self.cross_game_dir = self.memory_base / "_cross_game"
        self.db_path = self.cross_game_dir / "player_profile.db"
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create SQLite connection."""
        if self._conn is None:
            self.cross_game_dir.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_SCHEMA_SQL)
        return self._conn

    def close(self) -> None:
        """Close SQLite connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # === Distillation ===

    def distill_game(
        self,
        game_id: str,
        game_state,
        llm_call_fn=None,
    ) -> dict:
        """Distill human player behavior from a completed game.

        Args:
            game_id: Game ID
            game_state: GameState object with full game data
            llm_call_fn: Callable (prompt, max_tokens, temperature) -> str

        Returns:
            {"count": N, "matched_ids": set[int]}
        """
        _empty = {"count": 0, "matched_ids": set()}
        conn = self._get_conn()

        # Check if already distilled
        row = conn.execute(
            "SELECT id FROM game_history WHERE game_id = ?", (game_id,)
        ).fetchone()
        if row:
            log.info(f"[PlayerProfiler] Game {game_id[:8]}... already distilled, skipping")
            return _empty

        # Find human player
        human = None
        for p in game_state.players:
            if p.is_human:
                human = p
                break
        if not human:
            log.warning("[PlayerProfiler] No human player found")
            return _empty

        human_role = human.role
        winning_side = game_state.winner  # "good" or "werewolf"
        if not winning_side:
            log.warning("[PlayerProfiler] Game has no winner yet")
            return _empty

        human_side = _ROLE_SIDE.get(human_role, "good")
        human_won = (human_side == "good" and winning_side == "good") or \
                    (human_side == "wolf" and winning_side == "werewolf")
        game_outcome = "player_won" if human_won else "player_lost"

        # Collect human actions
        speech_records, vote_records, night_records = self._collect_human_actions(
            game_id, human, game_state
        )

        # If no meaningful actions, skip
        if not speech_records and not vote_records and not night_records:
            log.info(f"[PlayerProfiler] No human actions found in game {game_id[:8]}...")
            return _empty

        # LLM analysis
        if not llm_call_fn:
            log.info("[PlayerProfiler] No LLM function available, skipping distillation")
            return _empty

        # Determine perspectives first — pass to LLM so it generates per-perspective observations
        perspectives = self._determine_perspectives(human_role, human_won)

        observations = self._analyze_behaviors(
            human_role, game_outcome, winning_side,
            speech_records, vote_records, night_records,
            llm_call_fn,
            perspectives,
        )

        if not observations:
            log.info(f"[PlayerProfiler] No observations extracted from game {game_id[:8]}...")
            return _empty

        # Upsert each observation using its own perspective
        matched_ids: set[int] = set()
        new_count = 0
        for obs in observations:
            perspective = obs.get("perspective", "")
            if perspective not in perspectives:
                perspective = perspectives[0]  # fallback
            status, obs_id = self._upsert_observation(
                player_role=human_role,
                game_phase=obs.get("game_phase", "mid"),
                behavior_category=obs.get("behavior_category", "speech_pattern"),
                observation=obs.get("observation", ""),
                keywords=obs.get("keywords", ""),
                game_outcome=game_outcome,
                outcome_perspective=perspective,
                source_game_id=game_id,
            )
            if status == "matched" and obs_id is not None:
                matched_ids.add(obs_id)
            if status in ("matched", "inserted"):
                new_count += 1

        # Record in game_history
        conn.execute(
            """INSERT INTO game_history (game_id, human_role, game_outcome, winning_side, distilled_at, observation_count)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (game_id, human_role, game_outcome, winning_side, datetime.now().isoformat(), new_count),
        )
        conn.commit()

        log.info(f"[PlayerProfiler] Distilled {new_count} observations from game {game_id[:8]}...")
        return {"count": new_count, "matched_ids": matched_ids}

    def _collect_human_actions(
        self, game_id: str, human, game_state
    ) -> tuple[str, str, str]:
        """Collect human player's actions from game records.

        Returns:
            Tuple of (speech_records, vote_records, night_records) as formatted strings
        """
        game_dir = self.memory_base / game_id

        # Collect speeches from day records
        speech_lines = []
        day_record_dir = game_dir / "day_record_shared"
        if day_record_dir.exists():
            for round_file in sorted(day_record_dir.glob("round_*.md")):
                try:
                    content = round_file.read_text(encoding="utf-8")
                    round_num = round_file.stem.replace("round_", "")
                    # Extract human speeches
                    in_speech_section = False
                    for line in content.split("\n"):
                        if "## 发言" in line or "## 白天讨论" in line:
                            in_speech_section = True
                            continue
                        if line.startswith("## ") and in_speech_section:
                            in_speech_section = False
                            continue
                        if in_speech_section and human.name in line:
                            speech_lines.append(f"第{round_num}轮: {line.strip()}")

                    # Extract vote info
                    in_vote_section = False
                    for line in content.split("\n"):
                        if "## 投票" in line:
                            in_vote_section = True
                            continue
                        if line.startswith("## ") and in_vote_section:
                            in_vote_section = False
                            continue
                        if in_vote_section and human.name in line:
                            speech_lines.append(f"第{round_num}轮投票: {line.strip()}")

                except OSError:
                    continue

        # Also collect from game_state speeches/votes for current round
        for s in game_state.speeches:
            if s.get("player") == human.name:
                content = s.get("content", "")
                if content and not any(content in sl for sl in speech_lines):
                    speech_lines.append(f"第{game_state.round_number}轮: **{human.name}**: \"{content}\"")

        # Collect votes
        vote_lines = []
        for voter_id, target_id in game_state.votes.items():
            if voter_id == human.id:
                if target_id == "ABSTAIN":
                    vote_lines.append(f"第{game_state.round_number}轮: 旅行者选择弃权")
                else:
                    target = game_state.get_player_by_id(target_id)
                    target_name = target.name if target else target_id
                    vote_lines.append(f"第{game_state.round_number}轮: 旅行者投票给{target_name}")

        # Collect night actions
        night_lines = []
        night_dir = game_dir / human.id / "night_record"
        if night_dir.exists():
            for night_file in sorted(night_dir.glob("night_*.md")):
                try:
                    content = night_file.read_text(encoding="utf-8")
                    night_lines.append(content.strip())
                except OSError:
                    continue

        speech_text = "\n".join(speech_lines) if speech_lines else "无发言记录"
        vote_text = "\n".join(vote_lines) if vote_lines else "无投票记录"
        night_text = "\n".join(night_lines) if night_lines else "无夜间行动"

        return speech_text, vote_text, night_text

    def _analyze_behaviors(
        self,
        human_role: str,
        game_outcome: str,
        winning_side: str,
        speech_records: str,
        vote_records: str,
        night_records: str,
        llm_call_fn,
        perspectives: list[str] | None = None,
    ) -> list[dict]:
        """Use LLM to analyze human behavior patterns.

        Returns:
            List of observation dicts with perspective, game_phase, behavior_category, observation, keywords
        """
        role_cn = _ROLE_CN.get(human_role, human_role)
        human_won = game_outcome == "player_won"
        outcome_text = "旅行者获胜" if human_won else "旅行者失败"
        winning_cn = "好人阵营" if winning_side == "good" else "狼人阵营"

        # Build perspective-specific sections with concrete game context
        if not perspectives:
            perspectives = ["good_teammate"]
        perspective_sections = ""
        for p in perspectives:
            label, desc = _build_perspective_desc(p, human_role, human_won)
            perspective_sections += f"### 视角: {p}（{label}）\n{desc}\n\n"

        prompt = _DISTILL_PROMPT_TEMPLATE.format(
            human_role_cn=role_cn,
            outcome_text=outcome_text,
            winning_side_cn=winning_cn,
            speech_records=speech_records,
            vote_records=vote_records,
            night_records=night_records,
            perspective_sections=perspective_sections,
            perspective_keys=", ".join(perspectives),
        )

        try:
            response = llm_call_fn(prompt, 1000, 0.3)
        except Exception as e:
            log.error(f"[PlayerProfiler] LLM call failed: {e}")
            return []

        if not response:
            log.warning("[PlayerProfiler] Empty LLM response")
            return []

        # Parse JSON from response
        return self._parse_observations_json(response)

    @staticmethod
    def _parse_observations_json(response: str) -> list[dict]:
        """Parse LLM response into observation dicts."""
        # Try direct parse
        text = response.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines (``` markers)
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            result = json.loads(text)
            if isinstance(result, list):
                return [
                    obs for obs in result
                    if isinstance(obs, dict) and "observation" in obs
                ]
        except json.JSONDecodeError:
            pass

        # Try to find JSON array in text
        import re
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                if isinstance(result, list):
                    return [
                        obs for obs in result
                        if isinstance(obs, dict) and "observation" in obs
                    ]
            except json.JSONDecodeError:
                pass

        log.warning(f"[PlayerProfiler] Failed to parse observations JSON: {text[:200]}")
        return []

    @staticmethod
    def _determine_perspectives(human_role: str, human_won: bool) -> list[str]:
        """Determine outcome perspectives based on role side."""
        side = _ROLE_SIDE.get(human_role, "good")
        return _PERSPECTIVE_MATRIX.get(side, ["good_teammate"])

    # === Incremental Update ===

    def _upsert_observation(
        self,
        player_role: str,
        game_phase: str,
        behavior_category: str,
        observation: str,
        keywords: str,
        game_outcome: str,
        outcome_perspective: str,
        source_game_id: str,
    ) -> tuple[str, int | None]:
        """Insert or update an observation using keyword similarity matching.

        Distill-only: no confidence changes here. Confidence is adjusted
        exclusively by reinforce() and decay_unreinforced().

        Returns:
            ("matched", old_id)   — pattern recurred; old observation merged or noted
            ("inserted", new_id)  — brand-new observation inserted
            ("skipped", None)     — observation too short
        """
        if not observation or len(observation) < 5:
            return ("skipped", None)

        conn = self._get_conn()
        now = datetime.now().isoformat()

        new_kw_set = set(k.strip() for k in keywords.split(",") if k.strip())

        # Find existing observations with same (player_role, behavior_category, outcome_perspective)
        rows = conn.execute(
            """SELECT id, keywords, game_phase, observation, confidence, match_count
               FROM player_observations
               WHERE player_role = ? AND behavior_category = ? AND outcome_perspective = ?""",
            (player_role, behavior_category, outcome_perspective),
        ).fetchall()

        matched_old_id: int | None = None
        for row in rows:
            existing_kw_set = set(k.strip() for k in row["keywords"].split(",") if k.strip())
            jaccard = self._jaccard(new_kw_set, existing_kw_set)

            if jaccard > 0.3:
                matched_old_id = row["id"]
                if row["game_phase"] == game_phase:
                    # Same-phase match: merge text/keywords, increment match_count
                    better_obs = observation if len(observation) > len(row["observation"]) else row["observation"]
                    merged_kw = ",".join(new_kw_set | existing_kw_set)
                    conn.execute(
                        """UPDATE player_observations
                           SET observation = ?, keywords = ?,
                               match_count = match_count + 1, updated_at = ?,
                               source_game_id = ?
                           WHERE id = ?""",
                        (better_obs, merged_kw, now, source_game_id, row["id"]),
                    )
                    conn.commit()
                    return ("matched", matched_old_id)
                else:
                    # Different-phase match: insert new, but mark old as matched
                    break  # Fall through to insert

        # Insert new observation
        cursor = conn.execute(
            """INSERT INTO player_observations
               (player_role, game_phase, behavior_category, observation, keywords,
                game_outcome, outcome_perspective, confidence, match_count,
                created_at, updated_at, source_game_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0.5, 1, ?, ?, ?)""",
            (player_role, game_phase, behavior_category, observation, keywords,
             game_outcome, outcome_perspective, now, now, source_game_id),
        )
        conn.commit()
        new_id = cursor.lastrowid

        if matched_old_id is not None:
            # Different-phase match: pattern recurred, old gets reinforced
            return ("matched", matched_old_id)
        return ("inserted", new_id)

    @staticmethod
    def _jaccard(set_a: set, set_b: set) -> float:
        """Compute Jaccard similarity between two sets."""
        if not set_a and not set_b:
            return 0.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0

    # === Selective Loading (RAG) ===

    def query_observations(
        self,
        ai_role: str,
        current_phase: str,
        limit: int = 5,
        game_id: str | None = None,
    ) -> list[dict]:
        """Query relevant observations for an AI player.

        Args:
            ai_role: Current AI player's role (e.g., "werewolf", "seer")
            current_phase: Current game phase ("early", "mid", "late")
            limit: Max observations to return
            game_id: If provided, track which observations were served to this game

        Returns:
            List of observation dicts sorted by relevance
        """
        conn = self._get_conn()

        perspectives = _PERSPECTIVE_FOR_AI_ROLE.get(ai_role, ["good_teammate"])
        placeholders = ",".join("?" for _ in perspectives)

        rows = conn.execute(
            f"""SELECT id, player_role, game_phase, behavior_category, observation,
                       keywords, outcome_perspective, confidence, match_count
                FROM player_observations
                WHERE outcome_perspective IN ({placeholders})
                  AND confidence >= 0.2
                ORDER BY
                    CASE WHEN game_phase = ? THEN 0 ELSE 1 END,
                    confidence DESC,
                    match_count DESC
                LIMIT ?""",
            (*perspectives, current_phase, limit),
        ).fetchall()

        results = [dict(row) for row in rows]

        # Track which observations were served to this game
        if game_id and results:
            self._track_served(game_id, [r["id"] for r in results])

        return results

    def _track_served(self, game_id: str, observation_ids: list[int]) -> None:
        """Record which observations were served to a game for later RL reinforcement."""
        conn = self._get_conn()
        conn.executemany(
            "INSERT OR IGNORE INTO served_observations (game_id, observation_id) VALUES (?, ?)",
            [(game_id, oid) for oid in observation_ids],
        )
        conn.commit()

    def format_for_prompt(self, observations: list[dict], ai_role: str) -> str:
        """Format observations into a prompt-injectable string.

        Args:
            observations: List of observation dicts from query_observations
            ai_role: Current AI player's role

        Returns:
            Formatted markdown string for prompt injection
        """
        if not observations:
            return ""

        # Group by player_role
        by_role: dict[str, list[dict]] = {}
        for obs in observations:
            role = obs.get("player_role", "unknown")
            if role not in by_role:
                by_role[role] = []
            by_role[role].append(obs)

        role_cn = {
            "werewolf": "狼人", "seer": "预言家", "witch": "女巫",
            "hunter": "猎人", "villager": "村民",
        }

        phase_cn = {"early": "早期", "mid": "中期", "late": "后期"}
        category_cn = {
            "speech_pattern": "发言模式", "voting_pattern": "投票模式",
            "silence_pattern": "沉默模式", "accusation_pattern": "指控模式",
            "defense_pattern": "防御模式", "night_action": "夜间行动",
            "cooperation_pattern": "合作模式", "deception_pattern": "欺骗模式",
            "identity_claim": "身份声明",
        }

        parts = ["# 对旅行者的行为画像\n\n> 基于历史对局观察，以下是旅行者的行为倾向："]

        for role, obs_list in by_role.items():
            role_label = role_cn.get(role, role)
            parts.append(f"\n**当旅行者是{role_label}时**:")
            for obs in obs_list:
                conf = obs.get("confidence", 0.5)
                if conf >= 0.7:
                    conf_label = "高确信"
                elif conf >= 0.4:
                    conf_label = "中确信"
                else:
                    conf_label = "低确信"

                phase_label = phase_cn.get(obs.get("game_phase", ""), "")
                cat_label = category_cn.get(obs.get("behavior_category", ""), "")
                obs_text = obs.get("observation", "")

                suffix = f" ({phase_label}/{cat_label})" if phase_label and cat_label else ""
                parts.append(f"- [{conf_label}] {obs_text}{suffix}")

        return "\n".join(parts)

    # === Lifecycle Management ===

    # RL constants
    _REWARD = 0.10
    _PENALTY = 0.15
    _DECAY = 0.02

    def reinforce(
        self, game_id: str, winning_side: str, human_role: str | None = None,
        matched_ids: set[int] | None = None,
    ) -> dict:
        """Adjust confidence of observations based on game outcome.

        When matched_ids is provided (new flow), reinforces those specific
        observations identified by distill as recurring patterns.

        When matched_ids is None (backward compat), falls back to
        served_observations join.

        Args:
            game_id: Game that just finished
            winning_side: "good" or "werewolf"
            human_role: Human player's role this game (filters relevant obs)
            matched_ids: Observation IDs matched during distill (preferred)

        Returns:
            {"reinforced": N, "penalized": N, "adjusted_ids": set[int]}
        """
        conn = self._get_conn()
        now = datetime.now().isoformat()

        if matched_ids:
            placeholders = ",".join("?" for _ in matched_ids)
            rows = conn.execute(
                f"""SELECT id, outcome_perspective, confidence, player_role
                    FROM player_observations
                    WHERE id IN ({placeholders})""",
                list(matched_ids),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT po.id, po.outcome_perspective, po.confidence, po.player_role
                   FROM served_observations so
                   JOIN player_observations po ON po.id = so.observation_id
                   WHERE so.game_id = ?""",
                (game_id,),
            ).fetchall()

        reinforced = 0
        penalized = 0
        adjusted_ids: set[int] = set()

        for row in rows:
            # Only reinforce observations about the human's actual role
            if human_role and row["player_role"] != human_role:
                continue

            perspective = row["outcome_perspective"]
            # Determine which side consumes this observation
            if perspective in ("good_teammate", "good_opponent"):
                consuming_side = "good"
            elif perspective in ("wolf_teammate", "wolf_opponent"):
                consuming_side = "werewolf"
            else:
                continue

            old_conf = row["confidence"]
            if consuming_side == winning_side:
                new_conf = min(old_conf + self._REWARD, 1.0)
                reinforced += 1
            else:
                new_conf = max(old_conf - self._PENALTY, 0.0)
                penalized += 1

            adjusted_ids.add(row["id"])
            conn.execute(
                "UPDATE player_observations SET confidence = ?, updated_at = ? WHERE id = ?",
                (new_conf, now, row["id"]),
            )

        if reinforced or penalized:
            conn.commit()

        return {"reinforced": reinforced, "penalized": penalized, "adjusted_ids": adjusted_ids}

    def decay_unreinforced(
        self, current_game_id: str, exclude_ids: set[int] | None = None,
    ) -> int:
        """Decay confidence of observations not already adjusted by reinforce.

        Args:
            current_game_id: The game that just finished (new observations exempt)
            exclude_ids: Observation IDs already adjusted by reinforce (exempt)

        Returns:
            Number of observations decayed
        """
        conn = self._get_conn()
        now = datetime.now().isoformat()

        base_where = "source_game_id != ? AND confidence > 0.0"
        params: list = [current_game_id]

        if exclude_ids:
            placeholders = ",".join("?" for _ in exclude_ids)
            base_where += f" AND id NOT IN ({placeholders})"
            params.extend(exclude_ids)

        count = conn.execute(
            f"SELECT COUNT(*) FROM player_observations WHERE {base_where}",
            params,
        ).fetchone()[0]

        if count > 0:
            conn.execute(
                f"""UPDATE player_observations
                    SET confidence = MAX(confidence - ?, 0.0),
                        updated_at = ?
                    WHERE {base_where}""",
                [self._DECAY, now] + params,
            )
            conn.commit()
            log.info(f"[PlayerProfiler] Decayed {count} unreinforced observations")

        return count

    def prune_stale(self) -> int:
        """Delete observations with confidence below threshold.

        Returns:
            Number of observations pruned
        """
        conn = self._get_conn()

        count = conn.execute(
            "SELECT COUNT(*) FROM player_observations WHERE confidence < 0.15"
        ).fetchone()[0]

        if count > 0:
            conn.execute("DELETE FROM player_observations WHERE confidence < 0.15")
            conn.commit()
            log.info(f"[PlayerProfiler] Pruned {count} stale observations")

        return count

    def cleanup_old_games(self) -> int:
        """Remove old game directories, keeping the most recent ones.

        Returns:
            Number of game directories removed
        """
        if not self.memory_base.exists():
            return 0

        game_dirs = []
        for d in self.memory_base.iterdir():
            if d.is_dir() and d.name != "_cross_game":
                game_dirs.append(d)

        if len(game_dirs) <= KEEP_RECENT_GAMES:
            return 0

        # Sort by modification time, oldest first
        game_dirs.sort(key=lambda d: d.stat().st_mtime)
        to_remove = game_dirs[: len(game_dirs) - KEEP_RECENT_GAMES]

        removed = 0
        for d in to_remove:
            try:
                shutil.rmtree(d)
                removed += 1
                log.info(f"[PlayerProfiler] Removed old game directory: {d.name[:8]}...")
            except OSError as e:
                log.error(f"[PlayerProfiler] Failed to remove {d.name[:8]}...: {e}")

        return removed

    # === Stats ===

    def get_stats(self) -> dict:
        """Get profiler statistics.

        Returns:
            Dict with observation and game counts
        """
        conn = self._get_conn()
        obs_count = conn.execute("SELECT COUNT(*) FROM player_observations").fetchone()[0]
        game_count = conn.execute("SELECT COUNT(*) FROM game_history").fetchone()[0]
        avg_conf = conn.execute(
            "SELECT AVG(confidence) FROM player_observations"
        ).fetchone()[0]

        return {
            "total_observations": obs_count,
            "total_games_distilled": game_count,
            "avg_confidence": round(avg_conf, 3) if avg_conf else 0.0,
        }
