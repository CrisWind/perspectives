// 留空使用同源后端；本地独立启动前端时填写 http://localhost:8000。
// 部署前改为后端的 HTTPS 地址，并在后端配置允许的前端来源。
window.PERSPECTIVES_CONFIG = { apiBase: location.port === '5173' ? 'http://localhost:8000' : '' };
