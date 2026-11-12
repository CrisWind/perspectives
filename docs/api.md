# Perspectives 后端接口说明

写给前端同学。基础地址默认 `http://localhost:8000`，所有接口以 `/api` 开头，请求和响应都是 JSON。
启动后可以在 `/docs` 看到自动生成的接口页面并直接试用。

没有配置模型密钥时，后端使用模拟模型：流程、事件、数据结构和真实模型完全一样，只是发言内容是模板拼的。前端可以先对着它开发。

## 一、两种用法

### 快速模式（游园会）

```
POST /api/quick     {"preset_id": "grad_or_job", "mbti": "ENFP", "user_id": "可选"}
GET  /api/sessions/{id}/events    事件流
POST /api/sessions/{id}/feedback
```

`/api/quick` 一步完成创建和开始，返回会话对象。预设题有缓存时直接回放，否则现场生成。

### 深度模式

```
POST /api/sessions         创建，后端把用户的话拆成理由清单
POST /api/sessions/{id}/clarify   status 为 clarifying 时，提交追问的回答（也可以在这里修改理由）
POST /api/sessions/{id}/start     开始辩论
GET  /api/sessions/{id}/events    事件流
POST /api/sessions/{id}/interject 插话 / 对决策卡提异议
POST /api/sessions/{id}/feedback  反馈
```

创建会话的请求体：

```json
{
  "dilemma": "我在国企工作两年，想辞职转行做产品经理",
  "leaning": "倾向辞职",
  "reasons": ["现在的工作学不到东西"],
  "facts": ["存款够六个月"],
  "mbti": "ENFP",
  "user_id": "前端生成并保存在本地的匿名编号",
  "mode": "deep",
  "options": {"rounds": 3, "team": ["laomou", "zhangfang"], "quality_engine": true}
}
```

只有 `dilemma` 必填。`reasons` 不填时后端从 `dilemma` 里提取。`user_id` 用来记住用户的 MBTI、过去的决定和价值取向，不传就没有个人层的记忆。`options` 全部可省略。

## 二、会话对象

`POST /api/sessions`、`/api/quick`、`/clarify`、`GET /api/sessions/{id}` 都返回它。页面刷新后用 `GET /api/sessions/{id}` 可以恢复全部状态。

| 字段 | 说明 |
|---|---|
| `id` | 会话编号 |
| `status` | `clarifying` 等待回答追问；`ready` 可以开始；`running` 进行中；`completed` 已出决策卡；`failed` 失败 |
| `mode` | `quick` 或 `deep` |
| `category` | `study` 升学、`job` 求职、`switch` 跳槽、`city` 换城市、`other` |
| `dilemma` `leaning` `facts` | 难题、倾向、已知事实 |
| `reasons` | `[{"id": "r1", "text": "..."}]` |
| `questions` | `[{"id": "q1", "text": "...", "answer": null}]` |
| `team` | 角色列表，开始后才有，见下 |
| `card` | 决策卡，完成后才有，见第四节 |
| `claims` `utterances` `stances` `feedback` | 论点池、发言、立场账本、已提交的反馈 |
| `running` | 后台是否正在生成 |
| `last_seq` | 已产生的最后一个事件序号 |

角色：

```json
{"id": "laomou", "name": "老谋", "mbti": "INTJ", "role": "debater", "tagline": "凡事先想怎么输的战略家",
 "avatar": "♟️", "color": "#5B6CFF", "can_search": false, "provider": "deepseek",
 "framework": {"id": "premortem", "name": "事前验尸", "method": "..."}}
```

`role` 为 `future_self` 的是"未来的你"，每场都在，排在最后。`provider` 是这个角色用的模型。

论点（论证图的节点）：

```json
{"id": "c7", "round": 2, "persona_id": "zhangfang", "type": "rebuttal", "text": "...",
 "reason_id": "r1", "targets": "c3", "direction": "challenge", "sources": [], "echoed_by": ["gangjing"]}
```

- `type`：`new` 新论点、`rebuttal` 反驳、`support` 支持、`evidence` 补证据、`user` 用户插话（此时 `persona_id` 为 `user`）。
- `reason_id`：针对用户的哪条理由，可能为空。`targets`：针对哪条论点，可能为空。这两个字段就是论证图的边。
- `direction`：对用户倾向的作用，`challenge` 挑战、`support` 支持、`neutral` 补充视角。
- `sources`：查证来源的链接。后端保证只会出现搜索结果里真实存在的链接。
- `echoed_by`：第一轮里独立想到同一点的其他角色（"不谋而合"）。

## 三、事件流

`GET /api/sessions/{id}/events` 是 SSE。每条事件有 `id`（序号）、`event`（类型）、`data`（JSON，里面也带 `seq` 和 `ts`）。

```js
const es = new EventSource(`${BASE}/api/sessions/${id}/events?after=${lastSeq}`);
es.addEventListener("utterance.completed", e => { const d = JSON.parse(e.data); lastSeq = d.seq; /* ... */ });
es.addEventListener("done", () => es.close());
```

要点：

- 事件全部落库，任何时候连上都会从 `after` 之后补发，所以刷新和断线都不会丢。浏览器自动重连时会带 `Last-Event-ID`，后端同样认。
- 收到 `done` 后请主动 `close()`，否则浏览器会自动重连。
- 插话引发的新一轮也在同一条流里，以新的 `done` 结束。会后提异议时，先 `POST /interject`，再用 `after=last_seq` 重新连接。
- 不方便用 SSE 的地方（比如小程序）可以轮询：`GET /api/sessions/{id}/events?format=json&after=N`，返回 `{"events": [...], "status": "...", "running": true}`，每个事件是 `{"seq", "type", "data", "ts"}`。
- 发言不是逐字流式的。每条发言要先通过质量检查才能显示，所以是整条到达；打字机效果请前端自己做。等待期间有 `utterance.started` 可以显示"正在思考"。

事件类型：

| 事件 | data | 说明 |
|---|---|---|
| `status` | `{status}` | 状态变化 |
| `team.selected` | `{team, quality_engine}` | 组队完成 |
| `experience.applied` | `{items: [{id, pattern, persona_id}]}` | 这一场用上了经验库里的哪些经验 |
| `search.done` | `{persona_id, queries, results: [{title, url, snippet}]}` | 查证角色的联网搜索结果 |
| `round.started` | `{round, kind}` | `kind`：`independent` 独立作答、`cross` 交叉质询、`challenge` 挑战不足时的补充轮、`followup` 回应用户异议 |
| `utterance.started` | `{persona_id, round}` | 角色开始思考 |
| `quality.rejected` | `{persona_id, round, attempt, issues, speech}` | 发言被质量引擎打回重写 |
| `utterance.completed` | 见下 | 一条通过检查的发言 |
| `utterance.dropped` | `{persona_id, round, reason}` | 角色这一轮没有可用的发言 |
| `user.interjected` | `{claim}` | 用户的插话进入论点池 |
| `round.completed` | `{round}` | |
| `card.ready` / `card.updated` | `{card}` | 决策卡 / 回应异议后更新的决策卡 |
| `error` | `{message}` | 出错 |
| `done` | `{status, cached?}` | 这一段结束。`cached: true` 表示是回放的缓存 |

`quality.rejected` 的 `issues` 每项是 `{code, message, claim_text?, similar_to?}`，`code` 取值：`duplicate` 和已有论点重复、`stance_unjustified` 无理由改变立场、`persona` 偏离人设、`too_long` 超长、`no_challenge` 没有提出挑战、`format` 格式不可用。这是质量引擎在工作的直接证据，建议在界面或大屏上展示出来。

`utterance.completed`：

```json
{"id": "u6", "round": 2, "kind": "cross", "persona_id": "zhangfang", "speech": "给用户看的话",
 "claims": [ /* 论点对象 */ ], "echoes": [{"claim_id": "c2", "text": "..."}],
 "stance": {"position": "support", "previous": "conditional", "changed": true, "justified": true,
     "reverted": false, "persuaded_by": "c1", "confidence": 0.6, "note": "一句话说明"},
 "attempts": 2, "degraded": false, "persona_flag": false, "provider": "deepseek", "focus_reason_id": "r2"}
```

- `stance.position`：角色对"用户目前倾向"的态度，`support` / `oppose` / `conditional`。
- `stance.changed` 为真时，`persuaded_by` 是说服他的那条论点的编号，可以在图上画出来。
- `stance.reverted` 为真表示角色想改立场但说不出理由，账本没有承认。
- `attempts` 大于 1 说明被打回过。`degraded` 为真表示重写次数用完后降级通过。

## 四、决策卡

```json
{
  "reason_checks": [{"reason_id": "r1", "reason": "理由原文", "verdict": "shaky", "summary": "...", "claim_ids": ["c1", "c6"]}],
  "blind_spots": [{"text": "...", "claim_ids": ["c9"]}],
  "core_tradeoff": {"a": "稳定", "b": "成长", "summary": "..."},
  "recommendations": [{"condition": "如果……", "suggestion": "……"}],
  "next_steps": ["这周能做的小事"],
  "values": ["成长"],
  "stances": [{"persona_id": "laomou", "name": "老谋", "initial": "oppose", "final": "oppose", "note": "...",
        "changes": [{"round": 2, "from": "oppose", "to": "conditional", "persuaded_by": "c4", "justified": true}],
 "blocked_changes": 0}],
  "stats": {"claims": 22, "challenges": 18, "echoes": 0, "rewrites": 7, "rounds": 3, "experiences_used": 0}
}
```

`verdict`：`solid` 站得住、`shaky` 有条件成立、`weak` 站不住。`claim_ids` 保证都能在论点池里找到，可以做成点击跳转。`recommendations` 至少两条，都带条件。

## 五、插话和异议

```
POST /api/sessions/{id}/interject   {"text": "...", "reason_id": "可选", "claim_id": "可选，针对哪条论点"}
```

- 辩论进行中：返回 `{"queued": true}`，下一轮开始时进入论点池，角色必须回应。
- 决策卡出来之后：返回 `{"queued": false, "followup": true}`，相关角色回应一轮，然后发 `card.updated`。

## 六、反馈

```
POST /api/sessions/{id}/feedback
{"items": [{"claim_id": "c3", "label": "unexpected"}], "user_id": "可选"}
```

`label`：`unexpected` 没想到、`valuable` 有用、`useless` 没用。返回：

```json
{"accepted": 1, "ignored": 0, "learned": [{"id": 5, "pattern": "..."}], "reinforced": [], "skipped_support": 0}
```

`learned` 是系统刚从这次反馈里学到的新经验，`reinforced` 是被再次验证的旧经验。可以当场提示用户"系统记住了"。同一条论点重复提交只更新标签，不重复计数。

没有"同意结论"这类反馈项，这是有意的：系统只学"哪种挑战有价值"，不学"怎么让用户满意"。

## 七、其他接口

| 接口 | 说明 |
|---|---|
| `GET /api/health` | 服务状态、当前用的模型、是否能联网查证 |
| `GET /api/personas` | 全部角色和思维框架 |
| `GET /api/presets` | 预设难题卡和类别名称 |
| `GET /api/sessions/{id}/metrics` | 这一场的质量指标 |
| `POST /api/sessions/{id}/card` | 决策卡生成失败时重新生成 |
| `GET /api/users/{user_id}/profile` | 个人层记忆 |
| `GET /api/experiences?category=` | 经验库，按有用率排序 |
| `GET /api/evolution/log` | 进化日志，`layer` 为 `personal` / `group` / `system` |
| `GET /api/evolution/versions` | 角色补充准则的各个版本及状态 |
| `GET /api/stats` | 大屏用：场次、论点数、反馈数、经验数、打回次数、最近的进化动态、排名靠前的经验 |

管理接口（设置了 `ADMIN_TOKEN` 时需要请求头 `X-Admin-Token`）：

| 接口 | 说明 |
|---|---|
| `POST /api/evolution/personas/{persona_id}/propose` | 根据"没用"的反馈给角色起草补充准则 |
| `POST /api/evolution/versions/{version_id}/evaluate` | 后台跑门槛评测，过了才上线 |
| `POST /api/evolution/personas/{persona_id}/rollback` | 回滚到上一个版本 |

## 八、错误

- `400`：请求有问题，`{"detail": "原因"}`。
- `404`：会话或角色不存在。
- 辩论过程中出错：事件流里会有 `error`，随后 `status` 变为 `failed` 并发 `done`。如果论点已经产生、只是决策卡失败，可以调 `POST /api/sessions/{id}/card` 重试。
