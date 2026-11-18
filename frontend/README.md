# Perspectives 融合前端

当前入口：`frontend/index.html`。纯 HTML/CSS/JavaScript，不需要 npm 构建。

启动：在仓库根目录运行 `python -m http.server 5173 --directory frontend`，打开 http://localhost:5173 。

后端地址配置在 `frontend/config.js`；空字符串表示同源 `/api`，本地分离部署可设为 `http://localhost:8000`。不要在前端放模型 API key。

完整接手说明：`docs/FRONTEND_HANDOFF_20261118.md`。后端接口对照：`docs/BACKEND_FRONTEND_CONTRACT_20261118.md`。

示例播放固定走本地预录数据，不耗模型额度；“发起辩论”走真实后端。未连接后端时，仅能使用示例和视觉交互。
