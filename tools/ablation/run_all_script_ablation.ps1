# 电脑端纯脚本消融入口；不启动或操作 Android。
param([switch]$CheckOnly, [switch]$Smoke)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location -LiteralPath $taskRoot
$taskRuntime = Get-Content -Raw -Encoding UTF8 -LiteralPath 'config/runtime.json' | ConvertFrom-Json
$taskConfig = Get-Content -Raw -Encoding UTF8 -LiteralPath 'config/reader.json' | ConvertFrom-Json
$taskPython = [string]$taskRuntime.python
if (![IO.Path]::IsPathRooted($taskPython)) {$taskPython = Join-Path $taskRoot $taskPython}
$taskRunner = Join-Path $PSScriptRoot 'run_script_ablation.py'
$taskBook1 = Join-Path $taskRoot 'secrets/manga/[不可不可 (関谷あさみ)] インスタントサッキュ 丨 魅魔的速食性愛 [中国翻訳] [無修正] [DL版]'
$taskBook2 = Join-Path $taskRoot 'secrets/manga/[不可不可 (関谷あさみ)] インスタントサッキュ2 (オリジナル)｜魅魔的速食性愛 本篇2 [中文] [無修正]'
$taskOutput = Join-Path $taskRoot '~outputs-intermediate/evidence/script-ablation-20261028'
foreach ($taskPath in @($taskPython, $taskRunner, $taskBook1, $taskBook2)) {
    if (!(Test-Path -LiteralPath $taskPath)) {throw "缺少测试依赖：$taskPath"}
}
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:HF_HUB_OFFLINE = '1'
if ($CheckOnly) {
    & $taskPython -B -m py_compile $taskRunner
    if ($LASTEXITCODE -ne 0) {throw 'Python 脚本检查失败'}
    Write-Host '纯脚本入口检查通过；没有启动服务或测试。' -ForegroundColor Green
    exit 0
}
try {
    $null = Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:{0}/health" -f $taskConfig.reader_port) -TimeoutSec 3
} catch {
    Write-Host '正在启动电脑 OCR/TTS 服务…' -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $taskOutput | Out-Null
    $taskStartupLog = Join-Path $taskOutput 'service-start.log'
    & (Join-Path $taskRoot 'tools/start.ps1') *> $taskStartupLog
    if ($LASTEXITCODE -ne 0) {
        Get-Content -LiteralPath $taskStartupLog -Tail 30
        throw '电脑服务启动失败'
    }
    Write-Host '电脑服务已就绪。' -ForegroundColor Green
}
$taskArgs = @('-u', '-B', $taskRunner, '--book1', $taskBook1, '--book2', $taskBook2, '--output', $taskOutput)
if ($Smoke) {$taskArgs += '--smoke'}
Write-Host '开始电脑端消融测试；不操作 Android。' -ForegroundColor Cyan
Write-Host "结果目录：$taskOutput"
& $taskPython @taskArgs
if ($LASTEXITCODE -ne 0) {throw '纯脚本测试未完成；再次运行会跳过已完成的整页'}
