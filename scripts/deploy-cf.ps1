# 一条命令把 Worker 部署到 Cloudflare
#
# 前置：node/npm 已装、wrangler 已装（本机 4.119.0）
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\deploy-cf.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\deploy-cf.ps1 -SkipLogin
param(
    [switch]$SkipLogin,
    [string]$SessionFile = "C:\Users\szafx\Desktop\CodeSauce\projects\2026-11-19_xingchi-jb-ios16.2\work\session.json",
    [string]$ApiKey = "sk-d307351c89964e2772b36782a8e27792239df1c9e908a8475993cd1f2687293b"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$worker = Join-Path $root "worker"
Set-Location $worker

function Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m) { Write-Host "[√] $m" -ForegroundColor Green }
function Fail($m) { Write-Host "[x] $m" -ForegroundColor Red; exit 1 }

Step "检查 wrangler"
if (-not (Get-Command wrangler -ErrorAction SilentlyContinue)) { Fail "没装 wrangler：npm i -g wrangler" }
wrangler --version

if (-not $SkipLogin) {
    Step "检查 Cloudflare 登录状态"
    wrangler whoami *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "需要浏览器登录 Cloudflare…" -ForegroundColor Yellow
        wrangler login
    }
    Ok "已登录"
}

Step "准备 KV 命名空间 STATE"
$kvId = $null
try {
    $listJson = wrangler kv namespace list 2>$null | Out-String
    $list = $listJson | ConvertFrom-Json
    $hit = $list | Where-Object { $_.title -match "STATE" } | Select-Object -First 1
    if ($hit) { $kvId = $hit.id }
} catch { }

if (-not $kvId) {
    $out = wrangler kv namespace create STATE 2>&1 | Out-String
    Write-Host $out
    $m = [regex]::Match($out, 'id\s*=\s*"([0-9a-f]{32})"', 'IgnoreCase')
    if (-not $m.Success) { $m = [regex]::Match($out, '([0-9a-f]{32})') }
    if (-not $m.Success) { Fail "拿不到 KV id，请手工执行 wrangler kv namespace create STATE 后填入 wrangler.toml" }
    $kvId = $m.Groups[1].Value
}
Ok "KV id = $kvId"

Step "写入 wrangler.toml"
$toml = Get-Content "wrangler.toml" -Raw -Encoding UTF8
$toml = $toml -replace 'id\s*=\s*"[^"]*"', "id = `"$kvId`""
[IO.File]::WriteAllText((Join-Path $worker "wrangler.toml"), $toml, (New-Object Text.UTF8Encoding($false)))

Step "读取令牌"
if (-not (Test-Path $SessionFile)) { Fail "找不到 session.json：先跑一次登录脚本" }
$st = (Get-Content $SessionFile -Raw -Encoding UTF8 | ConvertFrom-Json).storage
if (-not $st.auth_token) { Fail "session.json 里没有 auth_token" }
Ok "access 长度 $($st.auth_token.Length)，refresh 长度 $($st.refresh_token.Length)"

Step "写入 Secret"
$st.auth_token   | wrangler secret put XC_SESSION
$st.refresh_token | wrangler secret put XC_REFRESH
$ApiKey           | wrangler secret put XC_API_KEY
("st_" + [guid]::NewGuid().ToString("N").Substring(0, 16)) | Tee-Object -Variable statusToken | wrangler secret put STATUS_TOKEN
Ok "Secret 已写入，STATUS_TOKEN=$statusToken"

Step "部署"
wrangler deploy
if ($LASTEXITCODE -ne 0) { Fail "部署失败" }
Ok "部署完成"

Step "立即触发一次验证"
$url = (wrangler deployments list 2>$null | Out-String)
Write-Host "worker 地址见上方输出，手动验证：" -ForegroundColor Yellow
Write-Host "  curl `"https://xingchi-daily.<你的子域>.workers.dev/run?token=$statusToken`""
Write-Host ""
Ok "完成。STATUS_TOKEN 请保存好，App/浏览器查状态要用。"
