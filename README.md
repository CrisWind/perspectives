# Perspectives · 多面

面向大学生选择场景的多视角决策 Agent。输入正在纠结的事、自己的倾向和理由，让思维方式不同的 AI 嘉宾逐条检验理由，最后得到理由体检、核心取舍和带条件的建议。

虹桥国际科技周黑客松，AI Agent 赛道。产品不替用户拍板，不投票判输赢，不做心理诊断。

本文按当前本地代码整理。队友已提交但尚未同步到本地的后端改动，列在文末“待同步与后续工作”中。

- [接口说明](docs/api.md)
- [前端改版记录与设计方案](docs/ui-redesign-plan.md)
- [界面截图与验收材料](docs/ui-design-v1/)
- [后端配置示例](backend/.env.example)

## 当前功能

### 三页前端流程

前端使用原生 HTML、CSS、JavaScript，无需 npm 构建。

1. **首页 `/`**：项目介绍、16 位 MBTI 嘉宾的性格卡轮转、卡片放大，以及内置样例辩论播放器。
2. **选择页 `/choose/`**：填写问题、倾向与理由，从 16 位嘉宾中选择 2–4 位，并可额外邀请一位思想家。默认选中四位嘉宾，支持键盘和触摸切换卡片。
3. **圆桌页 `/roundtable/`**：展示圆桌场景和角色发言，查看议题、辩论记录与决策卡，支持讨论中插话和会后提异议。

选择页会在浏览器本地保存草稿和阵容；圆桌页通过会话快照、事件历史和 SSE 接续讨论，刷新页面会恢复原会话。当前选择页创建的是深度模式会话，并设置 `skip_questions: true`，跳过后端澄清追问。

首页样例是内置的固定发言演示，与后端快速模式的预设题缓存回放分别实现。自定义角色接口已存在，当前三页前端尚未提供自定义角色编辑入口。

### 思想家嘉宾

当前语料库提供 **苏轼、萨特、王阳明**，选择页从 `GET /api/personas` 返回的 `sages` 动态加载可用名单。思想家额外占一席，不占 2–4 位普通嘉宾的名额；创建会话时通过 `options.sage` 传入其 ID。

- 角色设定与引文存放在 [sages.yaml](backend/app/content/sages.yaml)，思维框架存放在 [frameworks.yaml](backend/app/content/frameworks.yaml)。
- 后端按问题与引文的白话大意、主题匹配相关语料，再加入思想家提示词。
- 质量引擎检查发言中的引文是否出现在本次提供的原文中，匹配时忽略标点和空格；无法匹配的引文会触发重写。
- 当前加载规则是“存在非空引文即可出场”，`checked_by` 不作为启用门槛。库内匹配仅说明引用与所供语料一致，原著出处、译本、页码和人工核对仍需由内容维护者补全。
- 修改思想家语料也会改变内容版本，使旧的快速模式缓存失效。

## 本地启动

需要 Python 3.11 以上和 [uv](https://docs.astral.sh/uv/)。默认前端端口为 `5173`，后端端口为 `8000`。

### 1. 安装后端依赖

在项目根目录打开 PowerShell：

```powershell
cd backend
uv sync --locked
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
cd ..
```

按需填写 `backend/.env`。没有配置模型密钥时，后端使用内置模拟模型，发言为模板生成内容，可用于流程演示和前端联调。已有密钥时，可设置 `LLM_MOCK=1` 强制使用模拟模型。

**密钥只写在 `backend/.env` 里；该文件已被 Git 忽略，不要把密钥写入会提交的代码或文档。**

### 2. 启动前后端

Windows 下，在项目根目录运行：

```powershell
.\start-local.ps1
```

脚本使用 `backend/.venv312` 或 `backend/.venv` 中的 Python 启动服务，不会安装依赖；日志位于 `.scratch/`。

- 前端：[http://127.0.0.1:5173/](http://127.0.0.1:5173/)
- 后端接口文档：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- 健康检查：[http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health)

也可以分别在两个终端启动。终端一，从项目根目录进入后端：

```bash
cd backend
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

终端二，在项目根目录启动静态前端：

```bash
uv run --project backend python -m http.server 5173 --bind 127.0.0.1 --directory frontend
```

请通过 HTTP 服务访问前端。调整端口或部署地址时，需要同时核对 `frontend/ui/state.js`、圆桌页的 `apiBase` 与后端 `CORS_ORIGINS`；当前前端对 `5174/5175` 有连接后端 `8001` 的特殊映射。

## 模型与检索配置

模型使用 OpenAI 兼容接口。内置 DeepSeek、MiMo、通义千问、智谱、Moonshot、豆包、OpenAI 配置；填写对应的 `*_API_KEY` 后启用，其他服务可用 `LLM_EXTRA_PROVIDERS` 添加。具体变量见 [backend/.env.example](backend/.env.example)。

- 多家同时启用时，角色轮流分配模型；`PERSONA_PROVIDERS` 可限制角色使用的模型池。
- 质量检查和决策卡使用 `JUDGE_PROVIDER` 指定的模型；调用失败时会尝试其他已启用服务。
- 模型名、接口地址以服务商当前配置为准；豆包需要填写推理接入点 ID。
- 配置 `SEARCH_PROVIDER=tavily` 或 `bocha` 和 `SEARCH_API_KEY` 可启用联网查证。当前本地实现只把检索结果提供给查证角色。
- 后端提供写请求限流、并发会话上限、模型调用额度保护与安全拦截，参数见配置示例。上线前需配置 `ADMIN_TOKEN` 和允许访问的前端来源。

## 项目结构

```text
perspectives_j/
  README.md
  start-local.ps1          Windows 本地启动脚本
  frontend/
    index.html            首页
    choose/index.html     问题与阵容选择页
    roundtable/index.html 圆桌会议页
    ui/                   三页样式、嘉宾数据、草稿和会话适配逻辑
    roundtable.js         圆桌场景与交互
    roundtable.css        圆桌场景样式
    assets/               字体和圆桌图层（嘉宾形象与性格卡在 ui/assets/）
    verify-ui.cjs         Playwright 界面验收脚本
  backend/
    app/
      main.py             FastAPI 接口、SSE 事件与请求校验
      engine.py           澄清、组队、辩论、插话、决策卡和缓存编排
      quality.py          去重、立场、人设、来源与思想家引文检查
      evolution.py        个人档案、经验库和角色提示词进化
      metrics.py          评测指标
      prompts.py          提示词
      llm.py              模型接入
      mock.py             模拟模型
      search.py           联网查证
      store.py            SQLite 存储
      content.py          YAML 加载、组队和引文匹配
      content/            角色、框架、预设题和思想家语料
    scripts/              对照评测与快速模式预热
    tests/                后端测试
  docs/
    api.md                接口协议
    ui-redesign-plan.md    前端设计和改版记录
    ui-design-v1/         正式界面截图与验收记录
```

运行所需的代码、角色语料、图片、字体和字体许可均在本仓库内，不依赖同级个人资料目录或原始人物素材包。圆桌角色动画图片内嵌于 `frontend/roundtable/index.html`。第三方依赖按 `backend/uv.lock` 和 `frontend/package-lock.json` 安装，不提交虚拟环境或 `node_modules/`。

本机的 `blender-mcp/` 是素材制作工具，已忽略，不是应用运行依赖，也不会随仓库推送。旧设计预览、重复资源及一次性脚本不纳入交付；正式代码和验收记录保留。密钥、会话数据库、日志和缓存同样不提交。

### 一场辩论怎么进行

1. **澄清**：把用户输入拆成理由清单，缺少关键事实时最多追问两个问题。调用方可通过 `skip_questions` 跳过。
2. **组队**：默认按用户 MBTI 选择思维互补的四位角色，包含查证角色且框架不重复；显式传入 `options.team` 时按指定阵容组队，当前前端采用这种方式。思想家和自定义角色可额外加入。
3. **独立作答**：第一轮角色同时发言，互相看不到回答；独立想到的相同观点记为“不谋而合”。
4. **交叉质询**：角色看到上一轮论点池，并重点检验覆盖不足的理由；同轮重复论点会被拦下重写。`CROSS_PARALLEL=1` 可改成逐个发言。
5. **挑战底线**：针对用户倾向的挑战不足 `MIN_CHALLENGES` 时，增加一轮点名补充。
6. **决策卡与反馈**：生成理由体检、漏掉的视角、核心取舍和带条件的建议；支持插话、会后异议和论点反馈。

### 质量引擎与进化

每条发言在展示前经过论点去重、立场变化依据、人设、来源链接和思想家引文检查。决策卡还会检查论点编号、理由覆盖和建议条件。默认最多重写两次，仍不合格时丢弃重复论点、撤销无依据的立场变化，并记录标记。`options.quality_engine: false` 可关闭质量引擎，供对照评测使用。

进化分为三层：个人档案记录 MBTI、历史决定和关注点；群体经验库学习哪些挑战被标为“没想到”或“有用”；系统层手动起草角色补充准则，通过门槛评测后上线，可回滚。

反馈只记录论点是否有价值；支持用户倾向的论点即使收到正向反馈，也不进入经验库。补充准则若降低角色挑战数量，将无法通过上线门槛。当前门槛评测样本较小，候选准则仍需人工审阅。

## 测试与评测

推送前可在根目录执行 `python scripts/check_repository.py`，检查待共享文件中的本机绝对路径、误入的本地数据，以及 HTML、CSS 和角色清单的资源引用。提交前请先用 `git add -A` 同步本次整理造成的删除，再运行检查。

后端测试使用模拟模型，不消耗模型额度：

```bash
cd backend
uv run pytest
```

对照评测也在 `backend/` 下运行：

```bash
uv run python scripts/run_eval.py
uv run python scripts/run_eval.py --questions my.yaml
```

自定义题库格式参考 `app/content/presets.yaml`。评测比较单个 AI、单模型扮演多角色、多智能体关闭质量引擎、完整系统四种方案，使用统一抽取器、聚类器和裁判。结果位于 `backend/eval_out/`，包括 `summary.md`、`results.json` 和 `blind/` 盲评材料。配置真实模型后，评测会消耗额度。

前端验收脚本需要 Node.js 18 以上，并要求前端 `5173`、后端 `8000` 已启动。后端应使用模拟模式，避免验收消耗模型额度。在项目根目录运行：

```bash
cd frontend
npm ci
npx playwright install chromium
npm run test:ui
```

脚本使用项目内安装的 Playwright 与 Chromium，不依赖开发者电脑上的绝对路径。可用 `UI_URL` 和 `API_URL` 指定验收服务地址；这不修改页面自身的 API 地址配置。覆盖三页在桌面、平板和手机尺寸下的布局、卡片交互、草稿保存、会话创建与恢复等，截图和结果写入 `docs/ui-design-v1/production/`。

## 快速模式预热

游园会前在 `backend/` 目录运行：

```bash
uv run python scripts/warm_presets.py
```

脚本为预设题和去重后的 MBTI 队伍组合生成缓存，运行时输出实际场次数；真实模型预热会消耗时间和额度。命中缓存时快速模式直接回放，未命中时仍会现场生成。角色、思想家语料、预设题、相关模型配置或补充准则版本变化后，需要重新预热。

快速模式回放不会体现缓存生成之后的新经验；深度模式现场生成，会使用当前经验库。

## 待同步与后续工作

### 队友已提交、当前本地尚未同步

以下按队友提供的变更说明记录，需同步代码后再核对接口并接入前端：

- 会话新增 `track`：职业选择 `career`、生活烦恼 `life`，按赛道区分追问、角色规则和安全边界；新增校园生活、人际关系类别，预设题由 6 道增至 9 道。
- 创建会话支持 `profile`，包含专业、年级、经历、做事有劲与耗人的情况，并进行安全检查。
- 主持 AI 每轮决定继续、补查、问用户或收尾；新增 `moderator.decision`、`moderator.question`，轮数作为上限，并保留挑战数量底线。可通过 `options.moderator: false` 或 `MODERATOR=0` 关闭。
- `ask_user` 发问后讨论继续，用户通过现有插话接口回答；主持 AI 出错时辩论仍继续。
- 检索结果增加日期，向所有角色开放，主持 AI 可补查，一场最多搜索三次；前端需要适配 `search.done` 的 `date`。
- 决策卡新增 `to_verify`、`sources`、`roles_used`，资料来源由后端根据真实搜索结果生成。
- 新增岗位对照卡 `roles.yaml`、`GET /api/roles` 和 `roles.loaded`；现有四份草稿需补内容、真实来源和 `checked_by` 后才能生效。
- 评测增加“事实性论点带来源的比例”，数据库支持旧数据升级，快速模式缓存键变化后需重新预热。

### 后续重点

- **前端**：在当前三页界面中接入赛道、职业背景、主持过程、检索日期、待验证清单与来源链接。
- **内容**：补齐岗位对照卡；继续核对思想家语料的原著出处、版本、页码和主题标注。岗位卡与当前思想家库的启用规则不同。
- **联调**：验证真实模型的角色表现、主持策略和真实检索返回，区分发布时间与抓取时间。
- **评测**：补充有明确用户倾向的职业选择题，比较不重复论点、挑战数量和事实来源覆盖率。
- **后续能力**：用户自接 API 暂列后续任务。
