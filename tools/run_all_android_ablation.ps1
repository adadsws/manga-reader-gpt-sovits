# 在当前 PowerShell 窗口运行两本漫画的全部同页交错消融测试。
# 再次运行会跳过已完成的整页；未完成的页面会从第一轮重测。
param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot

$taskRuntime = Get-Content -Raw -Encoding UTF8 -LiteralPath 'config/runtime.json' | ConvertFrom-Json
$taskPython = [string]$taskRuntime.python
if (![IO.Path]::IsPathRooted($taskPython)) {
    $taskPython = Join-Path $taskRoot $taskPython
}
$taskRunner = Join-Path $PSScriptRoot 'run_interleaved_ablation.py'
$taskBook1 = Join-Path $taskRoot 'secrets/manga/[不可不可 (関谷あさみ)] インスタントサッキュ 丨 魅魔的速食性愛 [中国翻訳] [無修正] [DL版]'
$taskBook2 = Join-Path $taskRoot 'secrets/manga/[不可不可 (関谷あさみ)] インスタントサッキュ2 (オリジナル)｜魅魔的速食性愛 本篇2 [中文] [無修正]'
$taskOutput = Join-Path $taskRoot '~outputs-intermediate/evidence/android-ablation/interleaved-full-20261028'
foreach ($taskPath in @($taskPython, $taskRunner, $taskBook1, $taskBook2)) {
    if (!(Test-Path -LiteralPath $taskPath)) {throw "缺少测试依赖：$taskPath"}
}

$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:HF_HUB_OFFLINE = '1'
if ($CheckOnly) {
    & $taskPython -B -c "import sys; sys.path[:0]=['~temp/ocrdeps','.']; import cv2, numpy; from tools.test_android import find_reader_serial; print('ReaderAosp35: ' + find_reader_serial())"
    if ($LASTEXITCODE -ne 0) {throw 'Python 依赖或专用模拟器检查失败。'}
    Write-Host '测试入口检查通过；未开始批次。' -ForegroundColor Green
    exit 0
}
Write-Host '开始两本漫画的同页交错消融测试；已有完整页面会跳过。' -ForegroundColor Cyan
Write-Host "结果目录：$taskOutput"
Write-Host '请保持此窗口、电脑服务和 ReaderAosp35 运行，直到出现测试结束提示。'

$taskExit = 1
try {
    & $taskPython -u -B $taskRunner --book1 $taskBook1 --book2 $taskBook2 --output $taskOutput
    $taskExit = $LASTEXITCODE
} finally {
    $taskActivePath = Join-Path $taskRoot '~temp/reproduction/active.json'
    if (Test-Path -LiteralPath $taskActivePath) {
        $taskActive = Get-Content -Raw -Encoding UTF8 -LiteralPath $taskActivePath | ConvertFrom-Json
        if (!$taskActive.restored) {
            Write-Host '检测到未恢复批次，正在恢复安卓设置和媒体音量…' -ForegroundColor Yellow
            & (Join-Path $PSScriptRoot 'test_session.ps1') -Action restore
            if ($LASTEXITCODE -ne 0) {
                Write-Host '自动恢复失败，请排查后运行 tools/test_session.ps1 -Action restore。' -ForegroundColor Red
                $taskExit = 1
            }
        }
    }
}
if ($taskExit -ne 0) {
    Write-Host '测试未全部完成；已保存结果保留。修复问题后再次运行本脚本即可续跑。' -ForegroundColor Red
    exit $taskExit
}
Write-Host "两本漫画全部测试完成。汇总：$(Join-Path $taskOutput 'summary.json')" -ForegroundColor Green
