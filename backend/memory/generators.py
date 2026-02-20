"""Memory content generators - creates markdown/JSON content for memory files.

This module generates the actual text content that will be stored in memory files.
"""

from pathlib import Path
from typing import Any


ROLE_CN = {
    "seer": "预言家",
    "witch": "女巫",
    "werewolf": "狼人",
    "hunter": "猎人",
    "guard": "守卫",
    "villager": "村民",
}


def generate_day_record(
    round_num: int,
    morning_deaths: list[dict[str, str]],
    speeches: list[dict[str, str]],
    votes: dict[str, str],
    eliminated: dict[str, Any] | None,
) -> str:
    """Generate a day record markdown file.

    Args:
        round_num: Round number
        morning_deaths: List of dicts with 'name' of players who died last night
        speeches: List of dicts with 'player' and 'content' keys
        votes: Dict mapping voter name to target name
        eliminated: Dict with 'name' and 'votes' of eliminated player, or None if tie

    Returns:
        Markdown formatted day record
    """
    lines = [
        f"# 第{round_num}轮 - 白天记录",
        "",
        "## 晨间公告",
    ]

    # Deaths from last night
    if morning_deaths:
        for death in morning_deaths:
            lines.append(f"- **昨晚倒牌**: {death['name']} 死亡")
    else:
        lines.append("- **昨晚**: 平安夜，无人死亡")

    lines.append("")

    # Day discussion
    lines.append("## 白天讨论")
    lines.append("")

    for speech in speeches:
        player = speech["player"]
        content = speech["content"]
        lines.append(f'**{player}**: "{content}"')
        lines.append("")

    # Voting
    lines.append("## 投票")
    lines.append("")
    lines.append("| 投票人 | 目标 |")
    lines.append("|--------|------|")

    for voter, target in votes.items():
        lines.append(f"| {voter} | {target} |")

    lines.append("")

    # Voting outcome
    lines.append("## 投票结果")

    if eliminated:
        lines.append(f"- **放逐**: {eliminated['name']}，获得 {eliminated['votes']} 票")
    else:
        lines.append("- **平票**: 无人被放逐")

    lines.append("")

    lines.append(f"## 第{round_num}轮结束")

    return "\n".join(lines)


def generate_night_record(
    player_name: str,
    role: str,
    night_num: int,
    action: dict[str, Any] | None = None,
) -> str:
    """Generate a night record markdown file for a player.

    Args:
        player_name: Player name
        role: Player role
        night_num: Night number
        action: Dict with action details (varies by role)

    Returns:
        Markdown formatted night record
    """
    role_name = ROLE_CN.get(role, role)
    lines = [f"# 第{night_num}夜 - {role_name}行动", ""]

    if role == "villager":
        lines.append("夜间，无特殊能力，等待天亮。")

    elif role == "seer":
        if action:
            target = action.get("target", "未知")
            result = action.get("result", "未知")
            lines.append("## 我的行动")
            lines.append(f"- **查验目标**: {target}")
            lines.append(f"- **查验结果**: {result}")
            lines.append("")
            lines.append("## 我的思考")
            lines.append(f"- {action.get('thoughts', '已记录查验结果。')}")
        else:
            lines.append("本夜未进行查验。")

    elif role == "witch":
        if action:
            victim = action.get("victim", "未知")
            save_used = action.get("save_used", False)
            poison_used = action.get("poison_used", False)
            poison_target = action.get("poison_target", None)

            lines.append("## 我看到的")
            lines.append(f"- **今晚受害者**: {victim}")
            lines.append("")
            lines.append("## 我的决定")

            if save_used:
                lines.append(f"- **解药**: 已使用（救下 {victim}）")
            else:
                lines.append("- **解药**: 未使用")

            if poison_used:
                lines.append(f"- **毒药**: 已使用（毒杀 {poison_target}）")
            else:
                lines.append("- **毒药**: 未使用")

            lines.append("")
            lines.append("## 决策理由")
            lines.append(f"- {action.get('reasoning', '战略决策。')}")
        else:
            lines.append("本夜未采取行动。")

    elif role == "werewolf":
        if action:
            target = action.get("target", "未知")
            discussion = action.get("discussion", "团队已决定目标。")

            lines.append("## 团队讨论")
            lines.append(f"- {discussion}")
            lines.append("")
            lines.append("## 我们的决定")
            lines.append(f"- **击杀目标**: {target}")
            lines.append("")
            lines.append("## 决策理由")
            lines.append(f"- {action.get('reasoning', '战略击杀。')}")
        else:
            lines.append("本夜未执行击杀。")

    elif role == "guard":
        if action:
            target = action.get("target", "未知")
            lines.append("## 我的行动")
            lines.append(f"- **守护目标**: {target}")
            lines.append("")
            lines.append("## 我的思考")
            lines.append(f"- {action.get('thoughts', '已记录守护目标。')}")
        else:
            lines.append("本夜未进行守护。")

    elif role == "hunter":
        lines.append("夜间，保持警惕，随时准备开枪。")

    else:
        lines.append(f"第{night_num}夜 - 无特殊行动记录。")

    return "\n".join(lines)


def generate_personality_profile(personality_type: str, player_name: str) -> str:
    """Generate a personality profile markdown file.

    Accepts either a CharacterPreset ID (e.g. "hutao") or a legacy
    personality type string (e.g. "LOGICAL").  Legacy types fall back to
    a generic profile.

    Args:
        personality_type: Character preset ID or legacy personality type
        player_name: Player name

    Returns:
        Markdown formatted personality profile
    """
    from backend.ai.personality import get_character_by_id

    preset = get_character_by_id(personality_type)
    if preset is not None:
        return _profile_from_preset(preset)

    # Fallback for legacy / unknown types
    return _legacy_profile(personality_type, player_name)


def _profile_from_preset(preset) -> str:
    """Build a rich markdown profile from a CharacterPreset."""
    lines = [
        f"# {preset.name}（{preset.title}）- 角色档案",
        "",
        "## 角色背景",
        f"你是{preset.name}，{preset.title}。{preset.backstory}",
        "",
        "## 说话风格",
        preset.speech_style,
        "",
        "## 常用口头禅",
    ]
    for phrase in preset.example_phrases:
        lines.append(f'- "{phrase}"')

    lines += ["", "## 小习惯"]
    for quirk in preset.quirks:
        lines.append(f"- {quirk}")

    lines += [
        "",
        "## 狼人杀风格",
        preset.werewolf_approach,
        "",
        "## 核心特质",
        f"- **自信度**: {preset.confidence}",
        f"- **原型**: {preset.archetype}",
        "",
        "## 行为模式",
        "",
        "### 沟通风格",
    ]
    for t in preset.communication_traits:
        lines.append(f"- {t}")

    lines += ["", "### 决策风格"]
    for t in preset.decision_traits:
        lines.append(f"- {t}")

    lines += ["", "### 社交风格"]
    for t in preset.social_traits:
        lines.append(f"- {t}")

    return "\n".join(lines)


def _legacy_profile(personality_type: str, player_name: str) -> str:
    """Fallback profile for legacy personality types (HUMAN, etc.)."""
    lines = [
        f"# {player_name} - 角色档案",
        "",
        "## 核心特质",
        f"- **类型**: {personality_type}",
        f"- **原型**: 玩家",
        f"- **自信度**: 中等",
        "",
        "## 行为模式",
        "",
        "### 沟通风格",
        "- 随机应变",
        "",
        "### 决策风格",
        "- 权衡利弊后决定",
        "",
        "### 社交风格",
        "- 观察并适应",
    ]
    return "\n".join(lines)


def generate_initial_knowledge_summary(role: str, game_mode: str = "classic_6_witch") -> str:
    """Generate initial knowledge summary template for a role.

    Creates a dual-zone structure:
    - 经验攻略: Static strategy guide loaded from assets
    - 本局笔记: Dynamic zone for in-game reflections

    Args:
        role: Role name (seer, witch, werewolf, villager, hunter)
        game_mode: Current game mode (e.g., "classic_6_witch")

    Returns:
        Markdown template with playbook + game notes
    """
    role_name = ROLE_CN.get(role, role)

    playbook_path = Path(__file__).parent.parent / "assets" / "playbooks" / f"{role}.md"
    playbook = ""
    if playbook_path.exists():
        playbook = playbook_path.read_text(encoding="utf-8")

    # Build compact game rules filtered by current mode
    shared_knowledge = _build_game_rules(game_mode)

    # Get the dynamic template for this role
    template = _get_role_notes_template(role)

    return f"""# {role_name}知识库

## 经验攻略

{shared_knowledge}

{playbook}

---

## 本局笔记

{template}
"""


def _build_game_rules(game_mode: str) -> str:
    """Build compact game rules filtered by current game mode.

    Only includes rules relevant to the active mode — no cross-mode leakage.

    Args:
        game_mode: Current game mode key

    Returns:
        Compact markdown rules string
    """
    if "witch" in game_mode:
        mode_label = "女巫模式"
        roles_text = "2名狼人、1名预言家、1名女巫、2名村民"
        night_order = "1. 狼人协商击杀目标\n2. 女巫得知受害者，决定是否救人或毒人\n3. 预言家查验一名玩家身份"
        special_rules = (
            "- 女巫解药：看到受害者后可使用，首夜可自救\n"
            "- 女巫毒药：毒杀任意存活玩家，被毒者直接死亡\n"
            "- 女巫限制：每夜只能使用一种药（救人或毒人，不能同时）\n"
            "- 预言家：每晚验一人，得知好人或狼人"
        )
    elif "guard" in game_mode:
        mode_label = "守卫模式"
        roles_text = "2名狼人、1名预言家、1名守卫、2名村民"
        night_order = "1. 守卫选择守护一名玩家\n2. 狼人协商击杀目标\n3. 预言家查验一名玩家身份"
        special_rules = (
            "- 守卫：每晚守护一名玩家（含自己），被守护者不被狼人杀死\n"
            "- 守卫限制：不能连续两晚守护同一人\n"
            "- 预言家：每晚验一人，得知好人或狼人"
        )
    else:
        mode_label = "猎人模式"
        roles_text = "2名狼人、1名预言家、1名猎人、2名村民"
        night_order = "1. 狼人协商击杀目标\n2. 预言家查验一名玩家身份"
        special_rules = (
            "- 猎人：死亡时可开枪带走一名存活玩家（被投票或被狼杀均可开枪）\n"
            "- 猎人开枪：开枪后猎人身份公开（自证）\n"
            "- 预言家：每晚验一人，得知好人或狼人"
        )

    return f"""# 本局规则（{mode_label}）

**角色配置**: {roles_text}

**夜间顺序**:
{night_order}

**白天流程**: 晨间公告 → 轮流发言 → 公开投票（平票无人出局）

**胜利条件**: 好人方=所有狼人死亡；狼人方=狼人数≥好人数

**特殊规则**:
{special_rules}"""


def _get_role_notes_template(role: str) -> str:
    """Get dynamic game notes template for a role.

    Args:
        role: Role name (seer, witch, werewolf, villager, hunter)

    Returns:
        Dynamic notes template
    """
    templates = {
        "seer": """### 验证身份
（开局为空，随着验人逐步填充）

### 怀疑对象
（记录你的直觉判断）

### 策略笔记
- 决定何时公开身份
- 平衡隐藏与带队之间的关系
- 优先查验最可疑的玩家

### 信任网络
（随游戏进展逐步建立）
""",
        "witch": """### 行动风格
- **当前策略**: 隐藏身份，暂不公开
- **理由**: 先观察狼人动向再做决定

### 用药策略
- **解药**: 仅用于确认的重要角色或关键时刻
- **毒药**: 留到残局或确认狼人时使用

### 策略演进
（每轮记录策略变化）

### 带队笔记
（记录何时接手带队）
""",
        "werewolf": """### 团队分工
（与队友协调，查看共享记忆）

### 伪装计划
- **当前身份**: （跳神职？低调潜水？）
- **时间线**: （何时亮牌，声称什么身份）

### 谎言记录
（记录说过的谎话，保持前后一致）

### 当前挑战
（威胁、可疑玩家、风险）

### 下一步行动
（每轮调整策略）
""",
        "villager": """### 当前策略
- **角色扮演**: （假装神职？诚实村民？）
- **公开形象**: （自信？低调？激进？）
- **目标**: （保护真神？找狼人？求生？）

### 身份声明
（记录是否暗示过自己是神职）

### 观察记录
（对其他玩家的判断）

### 策略调整
（根据局势调整策略）
""",
        "guard": """### 守护记录
（开局为空，随着守护逐步填充）

### 守护策略
- **当前策略**: 优先守护高价值目标
- **自守条件**: 当自己暴露或被怀疑时考虑自守

### 威胁评估
（追踪最可能被杀的玩家）

### 协作笔记
（与其他神职配合）
""",
        "hunter": """### 身份公开计划
- **当前策略**: （隐藏？已公开？）
- **理由**: （什么时候跳身份最合适？）

### 开枪优先级
- **首选目标**: （被投票出局时带谁？）
- **备选目标**: （被夜间击杀时带谁？）

### 威胁评估
（追踪最危险的玩家）

### 协作笔记
（与其他神职配合）
""",
    }

    return templates.get(role, "（无该角色模板）")


def generate_initial_knowledge_json(role: str) -> dict[str, Any]:
    """Generate initial knowledge JSON data for a role.

    Args:
        role: Role name

    Returns:
        Initial JSON data structure
    """
    if role == "witch":
        return {
            "status": {
                "save_potion": True,
                "poison_potion": True,
                "last_updated_round": 0,
            },
            "usage_history": [],
        }
    elif role == "seer":
        return {
            "investigations": [],
        }
    elif role == "werewolf":
        return {
            "kills": [],
        }
    elif role == "guard":
        return {
            "status": {
                "last_guarded": None,
                "last_updated_round": 0,
            },
            "protection_history": [],
        }
    elif role == "hunter":
        return {
            "status": {
                "shot_fired": False,
                "last_updated_round": 0,
            },
            "usage_history": [],
        }
    else:
        return {}
