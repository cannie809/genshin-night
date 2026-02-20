"""Prompt templates for AI agent actions."""

import json
import random
from typing import TYPE_CHECKING

from backend.ai.personality import get_character_by_id
from backend.ai.sticker_registry import get_sticker_list

# Character-specific speech length limits (sentences, max_chars)
_SPEECH_LENGTH: dict[str, tuple[str, int]] = {
    "kazuha": ("1-2句极简短的话", 60),
    "chongyun": ("2-3句简洁的话", 80),
    "venti": ("2-3句话", 80),
    "barbara": ("2-3句话", 80),
    "zhongli": ("2-3句话", 100),
    "xingqiu": ("2-3句话", 100),
    "bennett": ("2-3句话", 100),
    "fischl": ("2-3句话", 100),
    "xiangling": ("2-3句话", 100),
    "hutao": ("2-4句话", 120),
}


if TYPE_CHECKING:
    from backend.models import GameState, Player


def _get_morning_announcement(game_state: "GameState") -> str:
    """Get the current round's morning death announcement from game events.

    Returns a prominent announcement string, or a peaceful-night hint if no deaths.
    """
    # Find morning events for current round
    for ev in reversed(game_state.events):
        if ev.round != game_state.round_number:
            continue
        if ev.type == "morning_death":
            return f"⚠️ {ev.message}——讨论必须围绕这一关键信息展开"
        if ev.type == "morning_safe":
            # Actually peaceful night — give mode-appropriate explanation
            roles_in_game = {p.role for p in game_state.players}
            reasons = []
            if "witch" in roles_in_game:
                reasons.append("女巫使用了解药救人")
            if "guard" in roles_in_game:
                reasons.append("守卫成功守护了被杀者")
            if reasons:
                return f"平安夜（无人死亡）通常意味着{'或'.join(reasons)}"
            return "平安夜（无人死亡）是正常的游戏结果"
    # Fallback: deduce from player list (in case events are missing)
    return "注意观察存活名单变化，分析昨晚发生了什么"


def _get_morning_event_text(game_state: "GameState") -> str:
    """Get the exact morning event text for the current round (for display in prompt)."""
    for ev in reversed(game_state.events):
        if ev.round != game_state.round_number:
            continue
        if ev.type == "morning_death":
            return ev.message
        if ev.type == "morning_safe":
            return ev.message
    return "（晨间公告暂未生成）"


def _get_kill_risk_hint(game_state: "GameState") -> str:
    """Get mode-appropriate kill risk analysis hint.

    Args:
        game_state: Current game state

    Returns:
        String describing possible risks of a kill (hunter/witch/guard specific)
    """
    roles_in_game = {p.role for p in game_state.players}
    hints = []
    if "hunter" in roles_in_game:
        hints.append("此人被杀后是否可能触发猎人开枪？")
    if "witch" in roles_in_game:
        hints.append("女巫是否可能救？")
    if "guard" in roles_in_game:
        hints.append("守卫是否可能守护此人？")
    return " ".join(hints) if hints else "击杀此人有哪些潜在风险？"


def _get_god_roles_label(game_state: "GameState") -> str:
    """Get a string listing god roles present in the current game mode.

    Args:
        game_state: Current game state

    Returns:
        String like "预言家/女巫" or "预言家/守卫"
    """
    role_cn = {"seer": "预言家", "witch": "女巫", "hunter": "猎人", "guard": "守卫"}
    god_roles = []
    seen = set()
    for p in game_state.players:
        if p.role in role_cn and p.role not in seen:
            god_roles.append(role_cn[p.role])
            seen.add(p.role)
    return "/".join(god_roles) if god_roles else "神职"


def build_system_prompt(player: "Player", memories: dict[str, any], game_state: "GameState") -> str:
    """Build system-level prompt with character identity and voice rules.

    This goes into the 'system' message to strongly anchor character voice.

    Args:
        player: Current player
        memories: Player memories dict
        game_state: Current game state

    Returns:
        System prompt string
    """
    preset = get_character_by_id(player.personality)
    profile = memories.get("profile", "暂无档案。")

    parts = [f"# 你的角色档案\n\n{profile}"]

    # Voice anchors + length constraint
    if preset and preset.voice_anchors:
        anchors = "、".join(f"「{a}」" for a in preset.voice_anchors)
        length_sent, length_chars = _SPEECH_LENGTH.get(preset.id, ("2-3句话", 100))
        parts.append(
            f"## 发言铁律\n\n**长度限制**: {length_sent}，不超过{length_chars}字。像真人玩家一样简洁有力地说话，不要写作文。\n**必须使用的声音标记**: {anchors}\n你的每次发言都应自然地融入以上标记中的至少1-2个。\n**每轮必须变化**: 每轮发言的开场方式、比喻手法、论证角度必须和上一轮不同。"
        )

    # Never do
    if preset and preset.never_do:
        never_items = "\n".join(f"- {n}" for n in preset.never_do)
        parts.append(f"**绝对不能做的事**:\n{never_items}")

    # Inter-character dynamics (filtered by alive players in this game)
    if preset and preset.inter_character_dynamics:
        alive_names = {p.name for p in game_state.alive_players}
        relevant = [(name, desc) for name, desc in preset.inter_character_dynamics if name in alive_names]
        if relevant:
            dynamics_text = "\n".join(f"- **{name}**: {desc}" for name, desc in relevant)
            parts.append(f"## 角色互动关系（本局中存在的角色）\n\n{dynamics_text}")

    # General rules
    parts.append("""## 通用规则

- 用中文发言，保持角色语气
- 绝不暴露你的真实角色、策略笔记、Playbook等元信息
- 像真实玩家一样说话，不要像AI分析报告""")

    return "\n\n".join(parts)


def _build_situation_context(player: "Player", memories: dict[str, any], game_state: "GameState") -> str:
    """Build game situation context (everything except character profile).

    This is the game-state part that goes into the 'user' message,
    extracted from build_base_prompt but without the profile section.

    Args:
        player: Current player
        memories: Player memories dict
        game_state: Current game state

    Returns:
        Situation context string
    """
    prompt = f"""# 你的内部情报（绝不可在发言中直接暴露以下内容）

**真实角色**: {player.role}

{memories.get("knowledge_summary", "暂无知识摘要。")}

"""

    if memories.get("knowledge_json"):
        prompt += f"""
**当前状态**: {json.dumps(memories["knowledge_json"].get("status", {}), indent=2, ensure_ascii=False)}

"""

    high_value = memories.get("high_value_insights", "")
    if high_value:
        prompt += f"\n**高效策略洞察**:\n{high_value}\n\n"

    key_facts = memories.get("event_index_facts", "")
    if key_facts:
        prompt += f"""
---

# 关键事实（不受窗口限制）

{key_facts}

"""

    prompt += """
---

# 近期游戏记录

## 白天记录（公开信息 - 所有玩家可见）

"""

    for day_record in memories.get("day_record", []):
        prompt += day_record + "\n\n"

    prompt += """
## 我的夜间行动（私密信息 - 仅我知道）

"""

    for night_record in memories.get("night_record", []):
        prompt += night_record + "\n\n"

    if player.role == "werewolf" and "shared" in memories:
        prompt += f"""
---

# 狼队共享记忆（绝密 - 白天绝对不能暴露）

{memories["shared"].get("strategy", "暂无团队策略。")}

**威胁评分**: {json.dumps(memories["shared"].get("threats", {}).get("threats", {}), indent=2, ensure_ascii=False)}

---

"""

    last_reflection = memories.get("last_reflection_shift", "")
    if last_reflection:
        prompt += f"""
---

# 上轮复盘

{last_reflection}

"""

    player_profile = memories.get("player_behavior_profile", "")
    if player_profile:
        prompt += f"""
---

{player_profile}

"""

    return prompt


def build_base_prompt(player: "Player", memories: dict[str, any], game_state: "GameState") -> str:
    """Build base prompt with player identity and memories.

    Args:
        player: Current player
        memories: Player memories dict
        game_state: Current game state

    Returns:
        Base prompt string
    """
    prompt = f"""# 你的身份

{memories.get("profile", "暂无档案。")}

---

# 你的内部情报（绝不可在发言中直接暴露以下内容）

**真实角色**: {player.role}

{memories.get("knowledge_summary", "暂无知识摘要。")}

"""

    # Add JSON data if available
    if memories.get("knowledge_json"):
        prompt += f"""
**当前状态**: {json.dumps(memories["knowledge_json"].get("status", {}), indent=2, ensure_ascii=False)}

"""

    # Add high-value insights if available
    high_value = memories.get("high_value_insights", "")
    if high_value:
        prompt += f"\n**高效策略洞察**:\n{high_value}\n\n"

    # Add key facts from event index
    key_facts = memories.get("event_index_facts", "")
    if key_facts:
        prompt += f"""
---

# 关键事实（不受窗口限制）

{key_facts}

"""

    prompt += """
---

# 近期游戏记录

## 白天记录（公开信息 - 所有玩家可见）

"""

    # Add day records
    for day_record in memories.get("day_record", []):
        prompt += day_record + "\n\n"

    prompt += """
## 我的夜间行动（私密信息 - 仅我知道）

"""

    # Add night records
    for night_record in memories.get("night_record", []):
        prompt += night_record + "\n\n"

    # If werewolf, add shared memory
    if player.role == "werewolf" and "shared" in memories:
        prompt += f"""
---

# 狼队共享记忆（绝密 - 白天绝对不能暴露）

{memories["shared"].get("strategy", "暂无团队策略。")}

**威胁评分**: {json.dumps(memories["shared"].get("threats", {}).get("threats", {}), indent=2, ensure_ascii=False)}

---

"""

    # Add last reflection
    last_reflection = memories.get("last_reflection_shift", "")
    if last_reflection:
        prompt += f"""
---

# 上轮复盘

{last_reflection}

"""

    # Add human player behavior profile (PlayerProfiler)
    player_profile = memories.get("player_behavior_profile", "")
    if player_profile:
        prompt += f"""
---

{player_profile}

"""

    return prompt


def get_werewolf_collab_prompt(
    wolf1: "Player",
    wolf1_mem: dict,
    wolf2: "Player",
    wolf2_mem: dict,
    shared_mem: dict,
    game_state: "GameState",
) -> tuple[str, str]:
    """Build prompt for werewolf collaboration.

    Returns:
        Tuple of (system_prompt, user_prompt)
    """
    alive_targets = [p.name for p in game_state.alive_players if not p.is_werewolf()]
    alive_all = [p.name for p in game_state.alive_players]

    wolf1_preset = get_character_by_id(wolf1.personality)
    wolf2_preset = get_character_by_id(wolf2.personality)

    wolf1_voice = ""
    wolf2_voice = ""
    if wolf1_preset:
        anchors1 = "、".join(f"「{a}」" for a in wolf1_preset.voice_anchors) if wolf1_preset.voice_anchors else ""
        wolf1_voice = (
            f"{wolf1.name}的语气特征: {wolf1_preset.speech_style[:80]}。声音标记: {anchors1}" if anchors1 else ""
        )
    if wolf2_preset:
        anchors2 = "、".join(f"「{a}」" for a in wolf2_preset.voice_anchors) if wolf2_preset.voice_anchors else ""
        wolf2_voice = (
            f"{wolf2.name}的语气特征: {wolf2_preset.speech_style[:80]}。声音标记: {anchors2}" if anchors2 else ""
        )

    system_prompt = f"""你是狼人杀的策略大师，正在协调两名狼人的夜间行动。
你的目标是做出最优的击杀决策和白天分工。
请严格按照推理步骤思考，最终输出合法JSON。

## 角色语气要求
每个狼人的reasoning必须用该角色自己的语气说话，不要用分析师口吻。
{wolf1_voice}
{wolf2_voice}"""

    user_prompt = f"""你在协调两名狼人：{wolf1.name} 和 {wolf2.name}。

## 狼队共享记忆

{shared_mem.get("strategy", "暂无策略。")}

**威胁评分**: {json.dumps(shared_mem.get("threats", {}).get("threats", {}), indent=2, ensure_ascii=False)}

## 各自视角

### {wolf1.name}（性格: {wolf1.personality}）

**简介**: {wolf1_mem.get("profile", "无简介。")[:300]}

**最近夜间记录**: {wolf1_mem.get("night_record", ["无"])[0] if wolf1_mem.get("night_record") else "无"}

**情报**: {wolf1_mem.get("knowledge_summary", "无情报。")[:400]}

### {wolf2.name}（性格: {wolf2.personality}）

**简介**: {wolf2_mem.get("profile", "无简介。")[:300]}

**最近夜间记录**: {wolf2_mem.get("night_record", ["无"])[0] if wolf2_mem.get("night_record") else "无"}

**情报**: {wolf2_mem.get("knowledge_summary", "无情报。")[:400]}

## 当前局势

- **回合**: {game_state.round_number}
- **存活玩家**: {alive_all}
- **可击杀目标（非狼人）**: {alive_targets}
- **存活狼人**: {[w.name for w in game_state.alive_werewolves]}

## 推理步骤（请逐步思考后再输出JSON）

### 第1步：威胁评估
逐一评估每个可击杀目标的威胁等级（高/中/低），考虑：
- 此人发言中是否展现了强逻辑推理能力？
- 此人是否可能是神职（{_get_god_roles_label(game_state)}）？有什么线索？
- 此人对狼队的投票引导力有多大？

### 第2步：击杀收益分析
对威胁最高的2-3个目标，分析击杀的收益与风险：
- 击杀此人后，白天的投票局势会如何变化？
- {_get_kill_risk_hint(game_state)}
- 哪个目标的击杀收益最大且风险最小？

### 第3步：白天分工
根据两名狼人的性格特点分配白天角色：
- {wolf1.name}更适合什么策略？（跳身份/潜水/带节奏/洗白）
- {wolf2.name}更适合什么策略？
- 两人的配合要点是什么？

### 第4步：最终决策
综合以上分析，做出最终决定。

**重要**: 思考完毕后，只返回一个合法的 JSON 对象，格式如下：
{{
  "wolf1_suggestion": "目标名字",
  "wolf1_reasoning": "用{wolf1.name}的语气说出选这个目标的理由（30字以内，必须符合角色人设）",
  "wolf2_suggestion": "目标名字",
  "wolf2_reasoning": "用{wolf2.name}的语气说出选这个目标的理由（30字以内，必须符合角色人设）",
  "final_target": "最终目标名字",
  "team_reasoning": "最终决定理由（25字以内）",
  "wolf1_daytime_role": "{wolf1.name}白天的角色和策略（25字以内，如'跳预言家带节奏'或'潜水观察不出头'）",
  "wolf2_daytime_role": "{wolf2.name}白天的角色和策略（25字以内）",
  "strategy_summary": "本轮整体合作策略（40字以内）"
}}

击杀目标从以下选择: {alive_targets}
"""

    return system_prompt, user_prompt


def get_seer_check_prompt(seer: "Player", memories: dict, game_state: "GameState") -> str:
    """Build prompt for seer investigation.

    Returns:
        Prompt string with investigation instruction
    """
    base = build_base_prompt(seer, memories, game_state)

    alive_others = [p.name for p in game_state.alive_players if p.id != seer.id]

    base += f"""
---

# 你的任务：选择查验目标

今晚你可以查验一名玩家的身份（狼人 或 好人）。

**存活玩家**: {alive_others}

**输出格式**:
先在 <analysis> 标签中简要分析（谁最值得验、为什么），再在 <target> 标签中只写一个玩家名字。

<analysis>
- 谁的发言最可疑？谁最需要确认身份？
- 已有的线索指向谁？
</analysis>
<target>
玩家名字（必须与存活玩家列表完全一致）
</target>"""

    return base


def get_witch_action_prompt(
    witch: "Player",
    memories: dict,
    killed_player_name: str | None,
    potion_status: dict,
    game_state: "GameState",
) -> str:
    """Build prompt for witch action.

    Returns:
        Prompt string with witch action instruction
    """
    base = build_base_prompt(witch, memories, game_state)

    alive_others = [p.name for p in game_state.alive_players if p.id != witch.id]

    base += f"""
---

# 你的任务：决定药水使用

## 今晚情报

- **狼人击杀目标**: {killed_player_name if killed_player_name else "无（平安夜）"}
- **解药状态**: {"可用" if potion_status.get("save_potion") else "已使用"}
- **毒药状态**: {"可用" if potion_status.get("poison_potion") else "已使用"}

## 可选行动

1. 使用解药（需要解药可用且有受害者）
2. 使用毒药（需要毒药可用）毒杀任意存活玩家
3. 不使用（保留药水）

**常识提醒**: 第一个夜晚通常应该救人（保住人数优势），除非你有充分理由不救（如被杀者高度可疑）。后期局势紧张时解药更珍贵，需要慎重考虑。

**存活玩家**: {alive_others}

**重要**: 只返回一个合法的 JSON 对象:
{{
  "action": "save" | "poison" | "skip",
  "target": "玩家名字"（仅 action 为 poison 时需要）,
  "reasoning": "简短理由"
}}

你的决定:"""

    return base


def get_hunter_shoot_prompt(hunter: "Player", memories: dict, game_state: "GameState") -> str:
    """Build prompt for hunter shooting.

    Returns:
        Prompt string with shooting instruction
    """
    base = build_base_prompt(hunter, memories, game_state)

    alive_others = [p.name for p in game_state.alive_players if p.id != hunter.id]

    base += f"""
---

# 你的任务：选择是否开枪

你已经死亡，可以在临死前开枪带走一名玩家，也可以选择不开枪。

**存活玩家**: {alive_others}

**输出格式**:
先在 <analysis> 标签中简要分析（2-3句话，不超过100字），再在 <target> 标签中写一个玩家名字，或写"不开枪"表示放弃。

<analysis>
- 根据已有信息，谁最可能是狼人？
- 是否有足够把握？开错枪的代价如何？
- 带走谁对好人阵营收益最大？
</analysis>
<target>
玩家名字（必须与存活玩家列表完全一致），或"不开枪"
</target>"""

    return base


def get_speech_prompt(player: "Player", memories: dict, game_state: "GameState") -> tuple[str, str]:
    """Build prompt for generating player speech.

    Returns:
        Tuple of (system_prompt, user_prompt) for speech generation
    """
    system_prompt = build_system_prompt(player, memories, game_state)
    user_prompt = _build_situation_context(player, memories, game_state)

    # Include speeches already given this round
    prior_speeches = game_state.speeches
    if prior_speeches:
        speeches_text = "\n".join(f'**{s["player"]}**: "{s["content"]}"' for s in prior_speeches)
        user_prompt += f"""
---

# 本轮已有发言

{speeches_text}

"""

    # Inject few-shot examples based on faction
    preset = get_character_by_id(player.personality)
    if preset:
        is_wolf = player.role == "werewolf"
        examples = preset.few_shot_wolf_speech if is_wolf else preset.few_shot_good_speech
        if examples:
            faction_label = "狼人阵营" if is_wolf else "好人阵营"
            examples_text = "\n".join(f'示例{i + 1}: "{ex}"' for i, ex in enumerate(examples))
            user_prompt += f"""
---

# 参考发言示例（{faction_label}风格，不要照抄，模仿语气和结构）

{examples_text}

"""

    # Build thinking style
    thinking_opener = ""
    if preset and preset.thinking_style:
        thinking_opener = preset.thinking_style
    else:
        thinking_opener = "内部推理："

    # Character-specific length
    length_sent, length_chars = _SPEECH_LENGTH.get(player.personality, ("2-3句话", 100))

    user_prompt += f"""
---

# 你的任务：白天发言

现在是第{game_state.round_number}轮白天讨论，轮到你发言了。
{f"你之前已有 {len(prior_speeches)} 位玩家发言。" if prior_speeches else "你是本轮第一个发言的玩家。"}

## 今日晨间公告
{_get_morning_event_text(game_state)}

## ⚠️ 长度硬性要求 ⚠️
发言必须控制在 **{length_sent}、不超过{length_chars}字**。超长发言会被截断。像真人桌游玩家一样简洁有力，不要写小作文。

## 发言要点
- 回应前面玩家的发言——赞同、反驳、质疑或补充
- 考虑要透露（或伪造）什么信息
- 考虑指控谁或保护谁

**常识推理**（发言前必须思考）:
- 如果有人跳{_get_god_roles_label(game_state)}等身份，认真评估其可信度
- {_get_morning_announcement(game_state)}
- 不要质疑常识性的逻辑推断

**重要规则**:
- 不要直接说"我是XX角色"，除非有明确战术理由
- 绝对不要提及"策略笔记""Playbook""知识库"等元信息
- 不要复述策略计划，只表达分析结论
- 自然地回应其他玩家的发言

**输出格式**:
先在 <analysis> 标签中进行内部推理（不会公开），再在 <speech> 标签中写出实际发言，最后在 <stickers> 标签中选择表情贴纸。

<analysis>
{thinking_opener}
- 如果我是狼人：队友有没有给我具体指令？
- 当前局势和前面发言的关键信息
- 这次发言的要点和立场
- 【长度检查】我的发言是否在{length_chars}字以内？
</analysis>
<speech>
你的发言（{length_sent}，不超过{length_chars}字）
</speech>"""

    # Append sticker selection menu if character has stickers
    sticker_names = get_sticker_list(player.personality)
    if sticker_names:
        menu = "、".join(f"「{s}」" for s in sticker_names)
        user_prompt += f"""
<stickers>
根据你在 <analysis> 中的内心活动，从上面列表中选 0-3 个最符合你此刻心情的: {menu}
只能从上面列表选，用 JSON 数组格式输出，不想用则输出 []
</stickers>"""

    return system_prompt, user_prompt


def get_guard_action_prompt(guard: "Player", memories: dict, game_state: "GameState") -> str:
    """Build prompt for guard protection decision.

    Returns:
        Prompt string with guard action instruction
    """
    base = build_base_prompt(guard, memories, game_state)

    alive_players = [p.name for p in game_state.alive_players]

    # Show last guarded player restriction
    last_guarded_hint = ""
    if game_state.last_guarded_player:
        last_guarded = game_state.get_player_by_id(game_state.last_guarded_player)
        if last_guarded:
            last_guarded_hint = f"\n**注意**: 你上轮守护了 **{last_guarded.name}**，本轮不能再守护此人。"

    base += f"""
---

# 你的任务：选择守护目标

今晚你可以守护一名玩家（包括你自己），被守护的玩家不会被狼人杀死。
你也可以选择不守护任何人。

**存活玩家**: {alive_players}
{last_guarded_hint}

**输出格式**:
先在 <analysis> 标签中简要分析（谁最可能被狼人杀、谁最值得保护、是否有理由不守护任何人），再在 <target> 标签中写一个玩家名字，或写"不守护"表示放弃守护。

<analysis>
- 谁是高价值目标？（神职通常是优先击杀对象）
- 上一轮的死亡模式能推测出什么？
- 守护自己还是队友更有价值？
- 是否有理由选择不守护？
</analysis>
<target>
玩家名字（必须与存活玩家列表完全一致），或"不守护"
</target>"""

    return base


def get_vote_prompt(player: "Player", memories: dict, game_state: "GameState") -> tuple[str, str]:
    """Build prompt for voting decision.

    Returns:
        Tuple of (system_prompt, user_prompt) for vote decision
    """
    system_prompt = build_system_prompt(player, memories, game_state)
    user_prompt = _build_situation_context(player, memories, game_state)

    alive_others = [p.name for p in game_state.alive_players if p.id != player.id]

    # Show recent speeches
    speeches_text = "\n".join(
        [
            f'**{s["player"]}**: "{s["content"]}"'
            for s in game_state.speeches[-10:]  # Last 10 speeches
        ]
    )

    # Wolf-specific reminder
    wolf_hint = ""
    if player.role == "werewolf":
        wolf_hint = """
- 狼队共享记忆中有投票指令吗？考虑是否执行
- 避免投给狼队友，除非是自保做戏"""

    user_prompt += f"""
---

# 你的任务：投票放逐

## 本轮所有发言

{speeches_text}

## 投票

根据以上讨论，今天应该放逐谁？

**存活玩家**（不含你自己）: {alive_others}

**你可以选择**:
- 投票给一名玩家（写玩家名字）
- 弃票（写"弃票"）— 如果你觉得信息不足、局势不明，或者投谁都不合适

**输出格式**:
先在 <analysis> 标签中简要分析（不超过150字），再在 <vote> 标签中只写一个玩家名字或"弃票"。

<analysis>
投票分析：{wolf_hint}
- 谁的发言最可疑？有哪些矛盾或可疑之处？
- 谁最符合我的阵营利益？
- 信息是否充分？不确定时弃票也合理
</analysis>
<vote>
玩家名字或"弃票"
</vote>"""

    return system_prompt, user_prompt
