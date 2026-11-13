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

打开 http://localhost:5173/。后端默认地址为 http://localhost:8000；部署到其他地址时需要调整 index.html 中的 apiBase 并配置后端跨域。

前端展示名映射旧角色名，角色 ID 保持兼容；后端人物名称仍需统一。自定义第五位辩手尚未接入。
