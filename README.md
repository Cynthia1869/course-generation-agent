# 制课生成 Agent

一个基于 `LangGraph + LangChain + FastAPI + Vue` 的制课生成系统。系统通过真实对话补全需求、接收资料、生成 Markdown 主稿，并在人工审核闭环里迭代改稿。

## 项目结构

- `backend/`: FastAPI、LangGraph、LangChain、文档解析、审计与接口
- `frontend/`: Vue 三栏画布页面
- `docs/`: 规格、接口和日志文档
- `infra/`: 基础环境说明

## 本地启动

### 1. 创建虚拟环境

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e backend[dev]
```

### 2. 启动后端

```bash
cp .env.example .env
uvicorn app.main:app --app-dir backend --reload
```

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

## 核心能力

- 真实对话式需求补全
- PDF / DOCX / MD / TXT 资料上传与解析
- 单课框架 + 案例 + 逐字稿 Markdown 生成
- 小模型 rubric 评分
- 人工逐条通过 / 编辑 / 驳回建议
- 基于 LangGraph interrupt 的恢复执行
- SSE 流式消息、节点状态、稿件更新、评分事件推送
- 结构化审计日志与版本 diff

## 开发约定

- Python 统一使用 `3.12`
- Git 分支规则：
  - `main`
  - `feature/*`
  - `fix/*`
  - `docs/*`
- 远程仓库通过 `gh` 管理
