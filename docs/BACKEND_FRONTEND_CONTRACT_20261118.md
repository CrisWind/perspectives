# 发给后端：融合前端接口对接 · 2026-11-18

本文件是现有后端与本轮前端的接口对照，不表示新增接口已经实现或完成真实模型联调。基于 main 的 a555467 与 docs/api.md；不需要重做已有的选队、思想家、自定义人物接口。

## 1. 服务地址与部署

前端 `frontend/config.js`：
```js
window.PERSPECTIVES_CONFIG = { apiBase: '' };
```
空值时请求同源 `/api`。分离部署填写后端 origin（例如 `https://api.example.com`，不要带 `/api` 或末尾斜杠）。前端请求会自行拼接 `/api`。后端允许实际前端 origin 的 CORS；SSE 路径不要被代理缓冲。模型 API key 只放后端环境变量。

## 2. 一次加载的目录接口

`GET /api/personas`：
```json
{
  "personas": [{"id":"laomou","name":"弈","mbti":"INTJ","framework":{"id":"premortem","name":"事前验尸"}}],
  "frameworks": [{"id":"premortem","name":"事前验尸"}],
  "sages": [{"id":"wangyangming","name":"王阳明","sage":true,"era":"明代"}]
}
```
示例只展示最少字段，后端可继续返回完整字段。自定义 framework 只用后端返回的 id，前端联网读取成功后更新选项。`sages` 是实际上线名单；前端保留三个预选按钮，即使断网也能选择并暂存在本地，不代表该思想家已在后端开放。

思想家 id 与静态立绘固定对应：
| id | 姓名 | 前端素材 |
|---|---|---|
| wangyangming | 王阳明 | assets/wangyangming.png |
| sushi | 苏轼 | assets/sushi.png |
| sartre | 萨特 | assets/sartre.png |

`GET /api/health`、`GET /api/presets` 延续原接口；本地示例使用打包的数据。

## 3. 创建真实深度会话

`POST /api/sessions`：
```json
{
  "dilemma":"我该考研还是先工作？",
  "leaning":"倾向工作",
  "reasons":["希望尽快经济独立"],
  "mbti":"INFP",
  "user_id":"浏览器匿名标识",
  "options":{
    "team":["zhangfang","laomou","gangjing","nuannuan"],
    "sage":"wangyangming"
  },
  "custom_persona":{
    "name":"我的嘉宾",
    "framework":"premortem",
    "mbti":null,
    "tagline":"",
    "style":"",
    "look":""
  }
}
```
- 常驻人物目前选满四位；id 沿用后端，不按中文显示名改接口 id。
- 不邀请思想家时省略 `options.sage`。
- 不加自定义人物时 `custom_persona` 为 null。
- 自定义人物只要求 name、framework，MBTI/简介/风格/look 收在“更多设置”。
- 四位 + 思想家 + 自定义人物，最多可能六位；以后端 team.selected 为最终阵容。
- 本前端真实模式等待用户确认理由后开辩，没有默认传 skip_questions。

响应返回现有 session 格式：id、status、dilemma、reasons、questions 等。status=safety 时进入安全流程，不进入理由确认。

## 4. 澄清与开始

`POST /api/sessions/{id}/clarify`
```json
{"answers":[{"id":"q1","answer":"我的回答"}],"reasons":["确认后的理由"]}
```
随后 `POST /api/sessions/{id}/start`，请求体 `{}`，再建立 SSE。

## 5. SSE

`GET /api/sessions/{id}/events?after=N`，沿用标准 SSE 的 event/data/id；data 中 seq 用来记录进度。

| event | 前端处理 |
|---|---|
| team.selected | 以 data.team 重建舞台，按 id 匹配已有立绘；u_ 人物用通用形象 |
| round.started | 更新轮次 |
| utterance.started | 显示某位正在思考 |
| utterance.completed | 接收整条 speech，排入舞台播放队列；完整句子分段，不伪称模型逐字流式 |
| quality.rejected | 目前只有重写提示；issues/speech 详情尚未完整呈现 |
| card.ready / card.updated | 显示精简结果及展开分析 |
| user.interjected | 展示用户插话 |
| safety | 停止动画与事件连接、隐藏辩论、展示后端求助信息 |
| done | 关闭连接；status=safety 不显示“决策卡已生成” |

思想家发言可继续携带 `quotes:[{id,text,source,meaning}]`。本交接版本新增记录区引文卡：展示原文、出处、含义，并标注“按他的思想推演，不是他本人说的现代的事”。不要让后端为了立绘另造发言类型。

注意：离线预录事件通常比舞台阅读速度更快，收到 card/done 时舞台队列可能尚未播完。后续可进一步同步结果揭示时机。

## 6. 插话与反馈

`POST /api/sessions/{id}/interject`
```json
{"text":"补充一个事实","reason_id":"r1","claim_id":"c1"}
```
reason_id、claim_id 可省略。台边“我也说一句”提交 text；记录中的“回应这条”支持指向论点。
- queued=true：提示已排队。
- followup=true：重新连接后续事件。
- `{ "queued":false, "safety":{...} }`：立即进入安全求助面板。

`POST /api/sessions/{id}/feedback`
```json
{"items":[{"claim_id":"c1","label":"valuable"}],"user_id":"浏览器匿名标识"}
```
支持 unexpected=没想到，valuable=有用，useless=没用。

## 7. 安全与限流

safety payload 沿用：
```json
{"message":"后端给出的支持文案","lines":[{"name":"热线名称","number":"号码","note":"说明"}],"tips":["建议"],"kind":"后端分类"}
```
这里仅说明结构，不提供或伪造实际热线。前端原样显示后端 message。

429 请继续返回中文字符串 detail（现有格式）；前端直接展示，不自动重试，finally 解除按钮加载。SSE 传输异常也关闭连接，不循环重连。不要为了此版改写已可直接显示的中文提示。

## 8. 后端同学需要确认的事项

1. 提供实际部署后端地址和前端 origin 的 CORS。
2. 确认三位思想家已在经过核对的 sages 语料中开放；空库时不要假装出场。
3. 用 LLM_MOCK=1 测四人、四人+思想家、四人+自定义、六人四种阵容。
4. 验证 safety 的创建/事件/插话三条路径，以及 429 后可再次操作。
5. 再做真实模型与真实检索联调。前端素材完成不等于这些验证已完成。

## 9. 明确尚未完成

页面刷新后从 session snapshot 恢复、完整质量打回详情、所有统计页、最终图谱化产出尚未在本融合版实现。GET /sessions/{id} 已存在，无需后端重复开发；由后续前端接手。

`POST /api/quick` 的现有 QuickIn 不接收 options.team/options.sage。本版“播放示例”走本地预录，不发真实请求；若要新增“真实快速模式自选阵容”，需双方另行统一接口，不能直接假设后端支持。

## 10. 停止按钮（本次手动包新增）

当前后端没有停止路由。按钮已能关闭本页SSE、清除舞台/气泡/字幕/倾听动画计时器和播放队列，保留已收到内容，解除按钮忙碌状态。真实模式明确提示后端可能仍在生成，不宣称已节省模型额度。

建议后端新增 POST /api/sessions/{id}/stop，请求体 {}，幂等取消会话生成任务，并返回最终status；也可由双方统一为每轮结束后停止。实现后前端需调用接口并确认成功，再写“讨论已结束”。此建议接口尚未实现，也没有在前端盲发请求。停止后是否生成已完成轮次的小结/决策卡请后端确认。
