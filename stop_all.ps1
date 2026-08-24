# LocalRAGServer 一键停止脚本（PowerShell）
$ErrorActionPreference = "SilentlyContinue"
foreach ($proc in @("python", "node", "vllm", "celery")) {
    Get-Process $proc -ErrorAction SilentlyContinue | Stop-Process -Force
}
Write-Host "[OK] 所有 LocalRAGServer 相关进程已终止。" -ForegroundColor Green
