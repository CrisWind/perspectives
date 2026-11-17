param([int]$FrontendPort = 5173)
$BackendPort = 8000
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$logDir = Join-Path $projectRoot '.scratch'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
function Test-LocalUrl([string]$Url) {
    try { return (Invoke-WebRequest $Url -TimeoutSec 3).StatusCode -eq 200 }
    catch { return $false }
}
$backendPython = Join-Path $projectRoot 'backend/.venv312/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $backendPython)) {
    $backendPython = Join-Path $projectRoot 'backend/.venv/Scripts/python.exe'
}
if (-not (Test-Path -LiteralPath $backendPython)) { throw '请先安装 backend 中的 Python 依赖。' }
if (-not (Test-LocalUrl "http://127.0.0.1:$BackendPort/api/health")) {
    Start-Process -FilePath $backendPython -ArgumentList @('-m','uvicorn','app.main:create_app','--factory','--host','127.0.0.1','--port',"$BackendPort") -WorkingDirectory (Join-Path $projectRoot 'backend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'backend.log') -RedirectStandardError (Join-Path $logDir 'backend-error.log') | Out-Null
}
if (-not (Test-LocalUrl "http://127.0.0.1:$FrontendPort/")) {
    Start-Process -FilePath $backendPython -ArgumentList @('-m','http.server',"$FrontendPort",'--bind','127.0.0.1') -WorkingDirectory (Join-Path $projectRoot 'frontend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'frontend.log') -RedirectStandardError (Join-Path $logDir 'frontend-error.log') | Out-Null
}
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    if ((Test-LocalUrl "http://127.0.0.1:$FrontendPort/choose/") -and (Test-LocalUrl "http://127.0.0.1:$BackendPort/api/health")) {
        Write-Output "页面：http://127.0.0.1:$FrontendPort/"
        $health = Invoke-RestMethod "http://127.0.0.1:$BackendPort/api/health"
        if ($health.mock) { Write-Output '辩论服务已连接（模拟模式）。' } else { Write-Output '辩论服务已连接。' }
        exit 0
    }
    Start-Sleep -Milliseconds 500
}
throw '服务未能启动，请查看 .scratch 中的日志。'
