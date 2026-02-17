# 狼人杀游戏前端

基于 React + TypeScript + Vite 构建的狼人杀游戏前端应用。

## 技术栈

- **框架**: React 18 + TypeScript
- **构建工具**: Vite 5.4
- **样式**: Tailwind CSS 4
- **状态管理**: Zustand
- **HTTP 客户端**: Axios

## 项目结构

```
frontend/
├── src/
│   ├── components/      # UI 组件
│   │   ├── GameBoard.tsx       # 游戏主界面
│   │   ├── PhaseIndicator.tsx  # 阶段指示器
│   │   ├── PlayerCard.tsx      # 玩家卡片
│   │   ├── PlayerGrid.tsx      # 玩家网格
│   │   ├── ActionPanel.tsx     # 操作面板
│   │   ├── GameLog.tsx         # 游戏日志
│   │   ├── CreateGameForm.tsx  # 创建游戏表单
│   │   └── index.ts            # 组件导出
│   ├── store/           # Zustand 状态管理
│   │   └── gameStore.ts # 游戏状态 store
│   ├── api/             # API 客户端
│   │   └── client.ts    # Axios 配置 + API 方法
│   ├── types/           # TypeScript 类型定义
│   │   └── game.ts      # 游戏相关类型
│   ├── App.tsx          # 主应用组件
│   ├── main.tsx         # 应用入口
│   └── index.css        # Tailwind CSS 导入
├── package.json
├── vite.config.ts
├── tailwind.config.js
└── tsconfig.json
```

## 核心模块

### 1. 类型系统 (`src/types/game.ts`)

定义了游戏的核心数据结构：
- `Role`: 角色类型（村民、狼人、预言家、女巫、猎人）
- `Phase`: 游戏阶段（夜晚、白天、投票、结束）
- `Player`: 玩家信息
- `GameState`: 游戏状态
- `GameAction`: 游戏动作

### 2. API 客户端 (`src/api/client.ts`)

封装了与后端 API 的交互：
- `createGame()`: 创建游戏
- `getGameState()`: 获取游戏状态
- `performAction()`: 执行游戏动作
- `startGame()`: 开始游戏

baseURL: `http://localhost:8000`

### 3. 状态管理 (`src/store/gameStore.ts`)

使用 Zustand 管理全局游戏状态：
- `gameId`: 游戏 ID
- `playerId`: 当前玩家 ID
- `players`: 玩家列表
- `currentPhase`: 当前阶段
- `currentRound`: 当前回合
- `events`: 游戏事件列表

## 开发命令

```bash
# 安装依赖
npm install

# 启动开发服务器
npm run dev

# 构建生产版本
npm run build

# 预览生产构建
npm run preview

# 运行 lint
npm run lint
```

## 开发服务器

启动后访问: http://localhost:5173

## 已实现的 UI 组件

所有核心 UI 组件已完成：

### 1. GameBoard (游戏主界面)
主容器组件，根据游戏状态显示不同界面：
- 未创建游戏时显示 CreateGameForm
- 游戏进行中显示完整游戏界面

### 2. PhaseIndicator (阶段指示器)
显示当前游戏阶段和回合数：
- 夜晚（蓝色）
- 白天（黄色）
- 投票阶段（红色）
- 游戏结束（灰色）

### 3. PlayerCard (玩家卡片)
显示单个玩家信息：
- 玩家名称
- 角色标签（仅自己可见）
- 存活/死亡状态
- AI 标识
- 选中状态高亮

### 4. PlayerGrid (玩家网格)
玩家卡片网格布局，支持：
- 响应式布局（1-3 列）
- 玩家选择交互
- 角色显示控制

### 5. ActionPanel (操作面板)
根据角色和阶段显示不同操作：
- **狼人夜晚**: 杀害按钮
- **预言家夜晚**: 查验按钮
- **女巫夜晚**: 解药/毒药按钮
- **白天/投票**: 投票按钮
- **通用**: 跳过按钮

### 6. GameLog (游戏日志)
显示游戏事件时间轴：
- 事件类型和描述
- 时间戳
- 自动滚动

### 7. CreateGameForm (创建游戏表单)
游戏初始化表单：
- 玩家名称输入
- 总玩家数选择（4-12）
- AI 玩家数选择
- 表单验证和错误提示

## 注意事项

- 确保后端服务运行在 `http://localhost:8000`
- Node.js 版本: 20.9.0（已配置兼容的 Vite 5.4.12）
