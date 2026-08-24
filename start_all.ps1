# LocalRAGServer 一键启动脚本（PowerShell）
# 启动：./start_all.ps1 [stub|local] [no-llm|with-llm]
#   stub    = 零依赖快速测试模式
#   local   = 本机 bge-m3 嵌入（需 uv sync --extra embed + GPU）
#   no-llm  = 不启动 LLM 服务（仅检索/上传可用）
#   with-llm= 尝试启动 vLLM（需 NVIDIA GPU + pip install vllm）

param(
    [ValidateSet("stub", "local")]
    [string]$EmbeddingBackend = "local",
    [ValidateSet("no-llm", "with-llm")]
    [string]$LlmMode = "no-llm"
)

$ErrorActionPreference = "Stop"
$ROOT = $PSScriptRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " LocalRAGServer 一键启动" -ForegroundColor Cyan
Write-Host " 嵌入后端: $EmbeddingBackend" -ForegroundColor Yellow
Write-Host " 大模型: $(if($LlmMode -eq 'with-llm'){'vLLM (GPU)'}else{'不启动'})" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan

# --- 清理残留进程 ---
Write-Host "`n[1/6] 清理残留进程..." -ForegroundColor Green
foreach ($proc in @("python", "node", "vllm", "celery")) {
    Get-Process $proc -ErrorAction SilentlyContinue | Stop-Process -Force
}
Write-Host "  已清理。" -ForegroundColor Gray

# --- 检查 Python 环境 ---
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] python 未找到，请先安装 Python 3.12+" -ForegroundColor Red
    exit 1
}

# --- 依赖检查（GPU 嵌入需要 torch + sentence-transformers）---
if ($EmbeddingBackend -eq "local") {
    Write-Host "[2/6] 检查 GPU 嵌入依赖 (torch + sentence-transformers)..." -ForegroundColor Green
    $torch_ok = python -c "import torch; print(torch.cuda.is_available())" 2>$null
    if (-not $torch_ok -or $torch_ok -match "False") {
        Write-Host "  [WARN] torch 未安装或无 CUDA。将切换为 stub 模式避免 500。" -ForegroundColor DarkYellow
        Write-Host "  如需 GPU 加速: uv sync --extra embed" -ForegroundColor Gray
        $EmbeddingBackend = "stub"
    } else {
        Write-Host "  CUDA 可用，torch OK（GPU 加速就绪）。" -ForegroundColor Green
    }
}

# --- 生成 .env ---
Write-Host "[3/6] 配置 .env ..." -ForegroundColor Green
$api_key = if (Test-Path "$ROOT\.env") {
    (Select-String -Path "$ROOT\.env" -Pattern "^RAG_API_KEY=").Matches[0].ToString().Split("=")[1].Trim()
} else { "" }
if (-not $api_key) {
    $api_key = (python -c "import secrets; print(secrets.token_urlsafe(32))" 2>$null)
}

@"
# ===== LocalRAGServer 自动生成的配置 =====
RAG_HOST=127.0.0.1
RAG_PORT=8000
RAG_DATA_DIR=data
RAG_API_KEY=$api_key
RAG_EMBEDDING_BACKEND=$EmbeddingBackend
RAG_EMBEDDING_MODEL=BAAI/bge-m3
RAG_EMBEDDING_DIM=1024
RAG_LLM_BASE_URL=http://127.0.0.1:9001/v1
RAG_LLM_MODEL=Qwen3-8B-AWQ
RAG_LLM_API_KEY=
RAG_QDRANT_URL=
RAG_RERANK_BACKEND=off
RAG_CELERY_BROKER_URL=filesystem://
"@ | Out-File -Encoding utf8 "$ROOT\.env"
Write-Host "  .env 已写入 (API Key: $($api_key.Substring(0,[Math]::Min(12,$api_key.Length)))...)" -ForegroundColor Gray

# --- 可选：启动 vLLM ---
if ($LlmMode -eq "with-llm") {
    Write-Host "[4/6] 检查 vLLM ..." -ForegroundColor Green
    $vllm_installed = python -c "import vllm" 2>$null
    if (-not $vllm_installed) {
        Write-Host "  [WARN] vLLM 未安装，跳过。如需启用: pip install vllm" -ForegroundColor DarkYellow
    } else {
        Write-Host "  正在后台启动 vLLM (Qwen/Qwen3-8B-AWQ, 端口 9001)..." -ForegroundColor Gray
        Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ROOT'; vllm serve Qwen/Qwen3-8B-AWQ --port 9001 --quantization awq" -WindowStyle Normal
    }
}

# --- 启动 API 服务 ---
Write-Host "[5/6] 启动 API 服务 (uvicorn, 端口 8000)..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ROOT'; .venv\Scripts\python.exe -m uvicorn apps.api.main:create_app --factory --host 127.0.0.1 --port 8000" -WindowStyle Normal

Start-Sleep -Seconds 2
# 检查是否启动成功
try {
    $response = Invoke-RestMethod -Uri "http://127.0.0.1:8000/healthz" -TimeoutSec 5
    Write-Host "  API 服务就绪!" -ForegroundColor Green
} catch {
    Write-Host "  [WARN] API 服务响应慢或失败，请查看上方终端。" -ForegroundColor DarkYellow
}

# --- 启动 Worker + Beat ---
Write-Host "[6/6] 启动 Worker + Beat (摄取任务)..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$ROOT'; .venv\Scripts\python.exe -m celery -A apps.api.main.celery_app worker --pool=solo --loglevel=info" -WindowStyle Normal
Start-Sleep -Seconds 1
Start-Process powershell -ArgumentLine "-NoExit", "-Command", "cd '$ROOT'; .venv\Scripts\python.exe -m celery -A apps.api.main.celery_app beat --loglevel=info" -WindowStyle Normal

# --- 启动前端 ---
Write-Host "`n[完成] 启动完毕!" -ForegroundColor Green
Write-Host "  - API:    http://127.0.0.1:8000" -ForegroundColor White
Write-Host "  - 管理端: http://127.0.0.1:5173 (开发模式)" -ForegroundColor White
if ($LlmMode -eq "with-llm") {
    Write-Host "  - LLM:    http://127.0.0.1:9001/v1 (vLLM)" -ForegroundColor White
} else {
    Write-Host "  - LLM:    未启动（纯检索功能可用；开启 LLM 需 llamacpp/vLLM）" -ForegroundColor DarkGray
}
Write-Host "`n如需停止所有服务: ./stop_all.ps1" -ForegroundColor DarkGray
