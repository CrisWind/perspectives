# 前端对接指南

接口细节以 [api.md](api.md) 为准，这份文档只讲：后端现在是什么状态、现有前端已经接了什么、还差什么。

## 1. 本地跑起来

```sh
# 终端 1：后端（没配密钥会自动用模拟模型，数据结构和真实模型一致）
cd backend && uv sync --locked
uv run uvicorn app.main:create_app --factory --port 8000

# 终端 2：前端
python -m http.server 5173 --directory frontend
```

- 打开 http://localhost:5173/ 。端口是 5173 时，[frontend/config.js](../frontend/config.js) 自动指向 `http://localhost:8000`。
- http://localhost:8000/docs 可以直接试每个接口。
- 部署：同源部署 `apiBase` 留空；跨域部署写后端 HTTPS 地址，并让后端配置 `CORS_ORIGINS`（默认 `*`）。
- 想测安全流程：后端设 `LLM_MOCK=1`，输入涉及自伤的内容。

## 2. 后端现状

已完成，模拟模型下全流程跑通并有测试：

- 快速模式（预设题，缓存回放）和深度模式（澄清、开辩、插话、决策卡）。
- 质量引擎：论点去重、立场账本、人设检查、来源核对，打回的发言会发 `quality.rejected`。
- 自定义第五位辩手（`custom_persona`）。
- 历史思想家嘉宾（`options.sage`，当前可选王阳明，发言带核实过的引文 `quotes`）。
- 安全拦截：自伤、伤害他人、遭受侵害时不开辩论，只发 `safety` 事件。
- 反馈、经验库、进化日志、大屏统计接口。

尚未验证：

- 还没用真实模型跑过，真实模型下发言耗时会比模拟长很多，界面要能撑住等待。
- 联网搜索（Tavily、博查）没用真实密钥验证过。
- 快速模式缓存需要提前跑 `scripts/warm_presets.py`，否则现场实时生成，也会慢。

## 3. 现有前端（单文件 [frontend/index.html](../frontend/index.html)）已经接了

| 功能 | 接口 / 事件 |
|---|---|
| 读取角色、框架、预设题、健康状态 | `GET /api/personas`、`/api/presets`、`/api/health` |
| 快速模式 | `POST /api/quick` |
| 深度模式：创建、澄清、开始 | `POST /api/sessions`、`/clarify`、`/start` |
| 事件流 | `EventSource` 监听 `team.selected` `round.started` `utterance.completed` `user.interjected` `quality.rejected` `card.ready` `card.updated` `safety` `done` |
| 插话 | `POST /interject` |
| 反馈三个按钮 | `POST /feedback` |
| 第五位自定义辩手 | `custom_persona` 字段 |
| 安全求助页 | `safety` 事件和会话 `status: "safety"` |

## 4. 还没接的（按优先级）

1. **思想家嘉宾**：`GET /api/personas` 的 `sages` 数组没用，创建会话也没传 `options.sage`。`utterance.completed` 里的 `quotes` 要在发言下方做引文卡，写明原文、出处，并标注"按他的思想推演，不是他本人说的现代的事"。这是后端文档里的明确要求。
2. **页面刷新恢复**：现在没有用 `GET /api/sessions/{id}` 恢复状态。建议把会话 id 存在 `sessionStorage`，刷新后拉会话对象，再用 `after=0` 重连事件流补发全部事件。
3. **`utterance.started`**：用来显示"正在思考"。真实模型下一条发言可能要等十几秒，不显示会像卡死。
4. **`error` 事件和 `429`**：收到 429 展示 `detail` 里的中文提示，不要自动重试。
5. **`quality.rejected` 的内容**：现在只显示一句固定文案。`issues[].code` 和 `speech` 可以展示出来，这是质量引擎在工作的直接证据，大屏演示很有用。
6. **决策卡细节**：`claim_ids` 做成点击跳转到对应论点；`stances[].changes` 里的 `persuaded_by` 可以在图上画出"谁说服了谁"。
7. **反馈结果**：`/feedback` 返回的 `learned` 可以提示"系统记住了这类追问"。
8. **大屏**：`GET /api/stats`、`/api/evolution/log` 前端还没用。

## 5. 几个容易踩的点

- **发言是整条到达，不是逐字流式**。打字机效果前端自己做。
- **收到 `done` 必须 `close()`**，否则浏览器会自动重连。插话或会后异议之后，先 `POST /interject`，再用 `after=last_seq` 重新连接。
- **`lastSeq` 要持久维护**，重连时带 `?after=`，后端会补发，不会丢也不会重复。
- **小程序或不能用 SSE 的环境**：轮询 `GET /events?format=json&after=N`。
- **安全流程是必须项**：`status` 为 `safety` 时停止展示辩论界面，只展示求助信息。文案用后端 `message` 原话，不要自己改成"你有危险"之类。
- **会话 id 等于访问凭证**，不要放进公开链接或日志。
- **`user_id`**：前端生成匿名编号存本地。创建会话的响应里有 `user_key`，读个人档案要带请求头 `X-User-Key`。
- **自定义辩手的快速模式不走缓存**，耗时接近深度模式，界面要给等待提示（现有前端已有）。
- **角色数量不固定为 4**：有自定义人物或思想家时是 5 或 6 位，布局不要写死。`team.selected` 里自定义人物 `custom: true`，思想家 `sage: true`。
- 管理接口（进化、种子经验）需要 `X-Admin-Token`，不要放进面向用户的页面。
