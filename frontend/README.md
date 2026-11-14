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

打开 http://localhost:5173/。本地 5173 端口默认访问 http://localhost:8000。部署时请在 `frontend/config.js` 设置线上 HTTPS 后端地址（同源部署可留空），并配置后端跨域。

可在表单中添加自定义第五位辩手，思维框架从后端读取。自定义快速模式实时生成，耗时较长。设置后端 `LLM_MOCK=1` 可用模拟模型测试安全流程。
