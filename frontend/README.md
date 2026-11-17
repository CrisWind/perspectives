# Perspectives 前端

在项目根目录启动后端：

```sh
cd backend
uv sync --locked
uv run uvicorn app.main:create_app --factory --port 8000
```

另开终端，在项目根目录运行：

```sh
python -m http.server 5173 --directory frontend
```

打开 http://localhost:5173/。部署到其他地址时需要调整 `ui/state.js` 和 `roundtable/index.html` 中的 API 地址并配置后端跨域。

人物图片和性格卡位于 `ui/assets/`，圆桌背景图层位于 `assets/roundtable/`，字体许可见 `licenses/`，不依赖仓库外的素材包。界面验收使用 `npm ci`、`npx playwright install chromium`、`npm run test:ui`，详细步骤见根目录 README。

前端展示名映射旧角色名，角色 ID 保持兼容；后端人物名称仍需统一。自定义第五位辩手尚未接入。
