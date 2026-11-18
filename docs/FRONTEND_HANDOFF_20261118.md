# 给前端接手同学：Perspectives 融合版交接 · 2026-11-18

## 当前代码放在哪里

入口为仓库 `frontend/index.html`。本手动上传包在原 frontend 基础上加入融合版，不要求删除队友文件；原首页另存为 frontend/index.teammate.html，原 choose、roundtable、ui 等文件仍保留，不修改 backend。请先在独立分支审核后再合并，避免覆盖同学正在进行的改动。

基线：main a5554679aa10d6137b78f7478c678633b850058c。
旧视觉来源：roundtable-handoff-lucy-v15 的完整 ZIP，含动作帧、示例、字体授权。
队友功能来源：main 的角色资料和卡面；环形轮转交互沿用其 ellipse/depth 思路，加入实际四人选择。

独立视觉预览：https://perspectives-merged-ring-lucy.extraordinaryhobby.chatgpt.site
该线上预览可能与交接分支存在少量接口修正差异（本分支补充了引文卡、真实框架更新和自定义人物不覆盖已选阵容）。仓库代码为交接依据。预览是静态展示，没有在其中部署 Python 后端。

## 如何启动

仓库根目录：
```bash
python -m http.server 5173 --directory frontend
```
打开 http://localhost:5173 。不需要 npm install/build。

后端按仓库 README 启动。修改 frontend/config.js 的 apiBase 指向后端 origin；同源代理则为空字符串。前端不填模型 key；它由后端保管。

## 用户流程

1. 首页：保留原人物形象、入场动作和标题。
2. 点击 Meet Your Council：进入二级选人页，首页不显示卡牌画廊。
3. 16张原性格卡围成椭圆轮转，点击转到前方；左右箭头、手机滑动、暂停均可。邀请/移出常驻人物，选满四位后可确认。
4. 思想家额外单选：王阳明、苏轼、萨特或不邀请；选择保存到本浏览器。
5. 确认阵容后进入圆桌。输入问题发起真实会话，或播放原固定阵容的离线示例。
6. 自定义人物默认名字+框架，其他内容在“更多设置”。它与思想家是两个独立入口。
7. 用户台边插话；完整记录收起，需要时打开。结果先显示精简取舍，完整内容展开查看。

## 与第一版及队友 main 相比

| 部分 | 本融合版 |
|---|---|
| 页面视觉 | 从深色舞台调整为用户参考图的雾蓝/淡紫/奶白，酒红重点色；保留原版人物和空间布局 |
| 人物选队 | 增加16张原卡面的环形选择，保留卡面原字体，没有再次二创图片 |
| 页面层级 | 首页→选队→圆桌三步；选队位于点击 Meet Your Council 之后 |
| 舞台 | 恢复较大的人物舞台、发言动作、倾听/思考/点头状态图轮换 |
| Reaction | 倾听角色轮换短反应气泡；它是舞台表现，不是额外的模型论点 |
| 发言 | 人物先到位，约0.8s后显示气泡；完整句子拆段，支持前后段/暂停 |
| 交接 | 下一角色预动作，随后切换发言焦点；保留旧版效果 |
| 思想家 | 三个按钮与三张新立绘，id映射后端；实际出场以 team.selected 为准 |
| 自定义 | 必填项收敛，其他选填折叠；通用形象兜底；不做自动“女娲”生成 |
| 插话/反馈/安全 | 沿用已有接口，保留三个反馈标签、safety 与 done 分支 |
| 决策卡 | 原版精简展示+展开分析；不是新版会议规划的完整思维导图 |

## 文件地图

| 路径 | 作用 |
|---|---|
| frontend/index.html | 原版页面与主要逻辑；包含大体积base64立绘、动作帧及fixtures |
| frontend/merge.js | 二级选队、椭圆轮转、思想家按钮、本地阵容保存、台边真实插话 |
| frontend/merge.css | 选队画廊、轮转卡、二级页面可见性、原/新布局覆盖规则 |
| frontend/light-theme.css | 最后加载的浅色主题；优先在这里改配色 |
| frontend/config.js | 后端 origin 配置；不放密钥 |
| frontend/roles.js | 队友16个角色结构化资料 |
| frontend/ui/assets/*-card.png | 16张原卡面（含原字体） |
| frontend/ui/assets/*.webp | 16位独立角色立绘 |
| frontend/assets/listening-poses.png | 四位主要人物倾听状态图，CSS按行列切换 |
| frontend/assets/*.png | 王阳明、苏轼、萨特透明立绘 |
| frontend/licenses/ | 随包字体许可 |
| docs/BACKEND_FRONTEND_CONTRACT_20261118.md | 后端接口字段与联调清单 |

卡面是位图，CSS改字体不会改变PNG里的文字。用户已经要求保留原卡面。本包保留早期网页卡面相关的少量未使用CSS及字体，后续可清理，但不要误删舞台所用字体。

## 人物素材与 id

主要人物：zhangfang=溯(ISTJ)，laomou=弈(INTJ)，gangjing=逆(ENTP)，nuannuan=晴(INFP)。中文显示名不改变后端 id。

思想家：wangyangming、sushi、sartre。`sageVisuals` 映射在 index.html 中；cast() 用 assets/{id}.png；merge.js 的 selectSage 更新预选舞台，真实响应 team.selected 覆盖为后端阵容。

思想家立绘是本次基于弈的材质、比例风格参考生成的静态透明 PNG，不是真实3D模型，也没有独立动作帧。只能沿用CSS的轻微动作，不应宣称它们已有口型动画。

## 演示与真实请求的边界（非常重要）

index.html 的 fetch/EventSource 是模式包装：demoPlayback=true 使用本地预录；“发起辩论”设为false后转发原生请求。“播放示例”明确切回离线。

示例固定四人，可以演示自定义人物的一条预设内容；它不模拟思想家的真实引文或意见。选了思想家，不等于固定预录示例会新增思想家台词。真实选队通过 options.team，思想家通过 options.sage，自定义通过 custom_persona。

不要把示例成功当成后端联调成功。启动时读取实际目录的独立 nativeFetch 会更新framework和后端可用名单；离线静态预览保留占位状态说明。

## 接手优先级

1. 配实际后端地址+CORS，先跑通深度创建→理由确认→开辩→事件→插话→结果。
2. 联调思想家+自定义同时出现（6人），核对布局与图像缩放。
3. 测安全三路径、429不重试/不锁按钮、SSE done关闭。
4. 补会话刷新恢复，使用已有GET /sessions/{id}，会话id是访问凭证，不放公开链接。
5. 完善质量打回issues、等待/播放/生成完成的时序、图谱化产出。
6. 后续拆分超大的index.html，外置fixtures和base64，减少传输，但不要在交接时同时大改状态机。

## 验证记录与限制

此前浏览器已验证：三级视觉流程、环形切换/邀请/移出、思想家选择与萨特立绘加入圆桌、原示例舞台动作。
本分支还做JavaScript语法和静态资源引用检查。真实部署后端、真实模型、搜索、刷新恢复未验证。后端已有mock测试覆盖不等于本融合前端通过全链路测试。

合并前请对照两份文档做一次后端mock演练。分支只改frontend与交接文档，不改backend。

## 手动上传包补充

已加入“停止讨论”按钮：停止本地播放、断开SSE并清除动画与播放队列；后端暂无stop接口，因此真实模式会说明服务器可能仍生成。不能将此功能描述成已经取消服务器任务。

本包保留队友旧文件，原首页在 frontend/index.teammate.html。两套入口不要混用；融合版入口仍为 frontend/index.html。旧页面使用原路径，后续清理旧资源须由接手同学确认。

## 本次网页上传的路径适配

为避免移动队友的卡面素材，融合版直接读取 frontend/ui/assets/ 的原图，而非另建 cards 目录。三个思想家素材放在 frontend/assets/wangyangming.png、sushi.png、sartre.png。旧 choose、roundtable、ui 目录未删除。
