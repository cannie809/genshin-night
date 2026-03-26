
## 一、CI/CD：从手动部署到自动化流水线

> 项目已有 Dockerfile + render.yaml，但"代码推到 main 后发生了什么"完全是黑箱。

---

### 现状盘点

| 组件 | 状态 | 说明 |
|------|------|------|
| Dockerfile | ✓ | python:3.13-slim，前后端单容器打包 |
| .dockerignore | ✓ | 排除 .git、node_modules、.env.local、tests/ |
| render.yaml | ✓ | Render PaaS，healthCheckPath: `/api/health` |
| pyproject.toml + uv.lock | ✓ | 依赖锁定，可重现构建 |
| GitHub Actions | ✗ | 完全缺失——无 CI，无自动部署 |
| 容器镜像仓库 | ✗ | 无独立镜像 registry |
| 多环境配置 | ✗ | 无 staging/production 分离 |

---

### 1. CI 流水线设计

**最小可行 CI**——推到 main 前至少跑这三件事：

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      - run: uv run pytest tests/ -v
  lint-frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: cd frontend && npm ci && npm run lint
  docker-build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t genshin-night:ci .
```

**教训**：
- CI 的第一优先级是**阻止坏代码合并**，而不是花哨的部署自动化。一个跑 `pytest` + `eslint` + `docker build` 的流水线就能拦住 80% 的问题。
- Docker build 在 CI 中跑一遍能发现本地 `.dockerignore` 遗漏（你以为忽略了的文件其实没忽略）。

---

### 2. CD 与环境管理

**本项目的部署路径**：`git push main` → Render 自动拉取 → Docker build → 部署。

Render 的 `render.yaml` 已经声明了所需环境变量（`sync: false`），但密钥需要在 Render Dashboard 手动设置：

```yaml
envVars:
  - key: PLAYER_KEYS
    sync: false        # 手动设置，不进版本控制
  - key: LLM_PROVIDER
    sync: false
  - key: OPENROUTER_API_KEY
    sync: false
```

**环境变量管理的三层原则**：
1. **开发环境**：`.env.local`（.gitignore 排除，本地专用）
2. **CI 环境**：GitHub Secrets（`${{ secrets.XXX }}`）
3. **生产环境**：平台级密钥管理（Render Dashboard / AWS Secrets Manager）

**教训**：
- 永远不要把 `.env` 提交到 Git。即使你之后删除，Git 历史里还有。用 `.gitignore` + `git-secrets` 双保险。
- `sync: false` 是 Render 的正确做法——声明"我需要这个变量"但不存值。CI/CD 配置应该是**可声明的**（声明需要什么），但**不可泄露的**（不包含实际值）。

---

### 3. Dockerfile 优化：多阶段构建

**当前问题**：单阶段构建把 Node.js、npm、构建工具都留在了最终镜像里，镜像偏大。

**改进方向**——多阶段构建分离"构建环境"和"运行环境"：

```dockerfile
# Stage 1: 前端构建
FROM node:20-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json .
RUN npm ci
COPY frontend/ .
RUN npm run build

# Stage 2: 后端运行
FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml uv.lock .
RUN pip install uv && uv sync --no-dev
COPY backend/ backend/
COPY --from=frontend-builder /app/frontend/dist frontend/dist
EXPOSE 8000
RUN useradd -m appuser && chown -R appuser /app
USER appuser
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

**三个改进点**：
1. **多阶段**：最终镜像不含 Node.js（省 ~200MB）
2. **非 root 用户**：`useradd appuser`（安全基线）
3. **缓存层优化**：先 COPY 依赖文件再 COPY 源码，依赖不变时跳过安装

---

## 二、ML Ops：LLM 应用的运维工程

> LLM 集成架构已经健全（多 provider、debug 日志、降级链），但缺乏**量化监控**——你不知道每局花了多少 token，也不知道解析失败率是升了还是降了。

---

### 已有的 ML Ops 基础

本项目在 LLM 工程化上做得不错，值得复用的模式：

| 模式 | 位置 | 说明 |
|------|------|------|
| 多 Provider 支持 | `agent.py:57-104` | openrouter/modelscope/openai，环境变量切换 |
| 连接层重试 | `agent.py:152-186` | 网络错误最多重试 3 次 |
| Debug 日志持久化 | `agent.py:305-339` | 每次 LLM 调用保存完整 prompt + response |
| 温度动态控制 | `personality.py` | 每个角色独立 temperature，投票时自动降低 0.1 |
| 4 层输出解析链 | `output_sanitizer.py` | 直接解析 → 代码块提取 → regex → 逐字段 |
| 演讲清洗 7 步管线 | `output_sanitizer.py:19-78` | 去标签/去英文/去元信息泄露/验证中文阈值 |
| Tier 预留架构 | `agent.py:112` | `tier="gameplay"` 标记，为未来模型分层做准备 |

---

### 4. Token 用量监控——你不追踪就不存在

**现状**：`_llm_call()` 返回 `response.choices[0].message.content`，但 `response.usage`（prompt_tokens、completion_tokens）被直接丢弃。

**应该做的**：

```python
# 在 _llm_call 中捕获 usage
if hasattr(response, 'usage') and response.usage:
    log.info(f"[LLM] {tier} | model={self.model} | "
             f"prompt={response.usage.prompt_tokens} "
             f"completion={response.usage.completion_tokens}")
```

更进一步——按游戏/轮次/动作类型聚合：

```python
@dataclass
class LLMUsageRecord:
    game_id: str
    round_number: int
    action: str          # speech / vote / reflection / wolf_collab
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    timestamp: datetime
```

**教训**：LLM API 的成本是**按 token 计费**的。如果你不追踪每次调用的 token 用量，就无法回答"一局游戏花多少钱"、"哪个动作最贵"、"切模型能省多少"这些运营问题。

---

### 5. 输出质量监控——降级链触发率是健康指标

**现状**：`output_sanitizer.py` 的 4 层解析链和 `agent.py` 的重试逻辑都有 `log.warning()`，但这些日志只能事后 grep，没有聚合指标。

**应该做的**：

```python
# 全局计数器（或用 prometheus_client）
PARSE_METRICS = {
    "direct_success": 0,      # 第 1 层直接解析成功
    "code_block_success": 0,  # 第 2 层代码块提取成功
    "regex_success": 0,       # 第 3 层 regex 成功
    "field_extract_success": 0,  # 第 4 层逐字段成功
    "total_failure": 0,       # 全部失败
}
```

然后暴露到 `/api/metrics` 或定期写入日志。

**关键健康指标**：
- **第 1 层成功率 > 85%**：模型输出质量正常
- **第 3-4 层触发率突增**：说明模型质量下降或 prompt 需要调整
- **total_failure > 0**：需要立即排查

**教训**：降级链是"优雅降级"的精髓，但如果你不监控每一层的触发频率，降级就变成了"静默掩盖问题"。你以为系统正常运行，实际上 30% 的 LLM 输出都在走 regex fallback。

---

### 6. Prompt 版本管理——可重现性的基础

**现状**：`prompts.py` 里的 prompt 模板没有版本标记。改了一个词、加了一句话，无法追溯"这个版本的 prompt 产生了什么效果"。

**最简单的版本管理**：

```python
# prompts.py 顶部
PROMPT_VERSIONS = {
    "system_prompt": "v2.1",
    "speech_prompt": "v1.3",
    "vote_prompt": "v1.2",
    "reflection_prompt": "v1.0",
}
```

在 debug 日志中一起记录：

```python
def _save_debug_log(self, ..., prompt_version: str = ""):
    # 文件名加入版本号
    # r1_speech_v1.3_attempt1_prompt.txt
```

**进阶做法**（当团队 > 1 人时）：
- Prompt 模板独立为 `.txt` 或 `.jinja2` 文件，纳入版本控制
- A/B 测试：同一动作随机选择不同版本的 prompt，比较输出质量
- 评估管线：离线跑 N 局游戏日志 × M 个 prompt 版本，量化对比

---

### 7. 多模型路由——不是所有动作都需要最贵的模型

**现状**：`tier="gameplay"` 标记已预留但未使用。所有 LLM 调用用同一个模型。

**分层策略**：

| Tier | 动作类型 | 模型需求 | 推荐 |
|------|---------|---------|------|
| **gameplay** | 发言、投票 | 需要角色一致性和推理 | GPT-4o-mini / Gemini Flash |
| **reflection** | 策略反思 | 需要长上下文分析 | 同上或更便宜的模型 |
| **utility** | 狼人协商、守卫选择 | 简单选择任务 | 最便宜的模型 |
| **profiling** | 玩家画像 distill | 摘要提炼 | 最便宜的模型 |

**实现**：

```python
MODEL_TIERS = {
    "gameplay": os.getenv("LLM_MODEL", "gpt-4o-mini"),
    "reflection": os.getenv("LLM_MODEL_REFLECTION", "gpt-4o-mini"),
    "utility": os.getenv("LLM_MODEL_UTILITY", "gpt-4o-mini"),
}

def _llm_call(self, prompt, ..., tier="gameplay"):
    model = MODEL_TIERS.get(tier, self.model)
```

**教训**：不是所有 LLM 调用都需要同等质量。发言需要创造性，投票需要逻辑性，但守卫选谁保护、狼人选谁攻击——这些可以用更便宜的模型。**按 tier 分配模型是最简单的成本优化**。

---

## 三、容器编排与部署架构演进

> 当前是"MVP 级部署"——单容器、内存状态、长轮询。能跑，但扛不住 10 个并发游戏。

---

### 当前架构的瓶颈

```
┌─────────────────────────────────────┐
│  FastAPI (单进程, 单容器)            │
│  ├─ games dict (内存) ←── 重启即丢  │
│  ├─ PrefetchManager (内存) ←── 同上 │
│  ├─ .memory/ (文件系统) ←── 不可共享│
│  └─ player_profile.db (SQLite)      │
│     └── 多实例并发写 = 锁冲突       │
└─────────────────────────────────────┘
        ↑ HTTP 长轮询 (1.5s / 5s)
        │ 每次返回完整 GameState
┌───────┴───────┐
│  React 前端    │
│  (打包在同容器) │
└───────────────┘
```

**核心问题**：

| 问题 | 影响 |
|------|------|
| 游戏状态只在内存 | 容器重启 = 所有进行中游戏丢失 |
| 无法水平扩展 | 加第二个容器看不到第一个的游戏 |
| SQLite 单写锁 | 多实例部署时 player_profiler 数据损坏 |
| 长轮询 O(n) | 10 个游戏 × 每秒 1-5 个请求 = 50 QPS 空转 |

---

### 8. Phase 1：最小可行改进

**目标**：支持 50 个并发游戏，零数据丢失。

```
用户 ──→ FastAPI ──→ Redis (游戏状态)
                  ──→ PostgreSQL (持久化 + 画像)
                  ──→ 文件系统 or S3 (游戏记录)
```

**三个改动**：

1. **游戏状态迁到 Redis**
   - `games[game_id]` → `redis.set(f"game:{game_id}", state.json())`
   - TTL 24 小时自动清理，游戏结束时主动删除
   - 容器重启后从 Redis 恢复

2. **SQLite 迁到 PostgreSQL**
   - `player_profile.db` → PostgreSQL 表
   - 解决多实例并发写锁问题
   - ACID 保证，自动备份

3. **长轮询升级 WebSocket**（前端改动最大）
   - 延迟：1.5s → <100ms
   - 带宽：完整 state → 仅变更事件
   - 但这一步可以延后——先做后端持久化

---

### 9. 健康检查与可观测性

**render.yaml 声明了 `/api/health`**——确保它不只是返回 200，还要检查依赖：

```python
@app.get("/api/health")
async def health():
    checks = {
        "status": "ok",
        "games_active": len(games),
        "memory_mb": process.memory_info().rss / 1024 / 1024,
    }
    # 如果有 Redis / DB，也检查连通性
    return checks
```

**日志策略**——三层：
1. **结构化日志**（JSON 格式）：方便 ELK/CloudWatch 聚合
2. **LLM debug 日志**（已有）：prompt + response 持久化
3. **业务指标**（待加）：每局游戏耗时、每轮 LLM 调用次数、降级触发率

---

### 10. 从单容器到生产级的演进路径

| 阶段 | 架构 | 并发游戏 | 月成本 |
|------|------|---------|--------|
| **MVP**（现在） | 单容器 + 内存 + SQLite | ~5 | $0（Free Tier） |
| **Phase 1** | 单容器 + Redis + PostgreSQL | ~50 | ~$30 |
| **Phase 2** | 多容器 + LB + 消息队列 | ~500 | ~$200 |

**不要跳阶段**。Phase 1 的 Redis + PostgreSQL 就能覆盖绝大多数场景。只有当你确认有 50+ 并发用户时，才需要考虑 Phase 2 的多实例 + 消息队列。

**教训**：
- 过早引入 Kubernetes 是创业项目的常见死法。先用 Render/Railway 这种 PaaS 跑通，有流量了再迁。
- 但 **持久化** 不能等——用户进行中的游戏因为你部署新版本而丢失，这是不可接受的体验。Redis 是最小成本的解决方案。

---