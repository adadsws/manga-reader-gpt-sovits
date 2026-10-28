# 两部样本的正式 Android 消融入口；各批次单独落盘，可在恢复后续跑。
param(
    [Parameter(Mandatory=$true)][string]$Book1,
    [Parameter(Mandatory=$true)][string]$Book2,
    [string]$Output='~outputs-intermediate/evidence/android-ablation/full-20261028'
)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskOutput=[IO.Path]::GetFullPath($Output)
$taskEvidence=[IO.Path]::GetFullPath((Join-Path $taskRoot '~outputs-intermediate/evidence'))
if (!$taskOutput.StartsWith($taskEvidence + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) {throw '输出目录必须位于中间证据目录内。'}
foreach ($taskBook in @($Book1,$Book2)) {
    if (!(Test-Path -LiteralPath $taskBook -PathType Container)) {throw "样本目录不存在：$taskBook"}
}
New-Item -ItemType Directory -Path $taskOutput -Force | Out-Null
$env:PYTHONUTF8='1'
$env:READER_NO_PAUSE='1'
$taskMatrices=@()
$taskSources=@($Book1,$Book2)
for ($taskIndex=0;$taskIndex -lt $taskSources.Count;$taskIndex++) {
    $taskMatrix=Join-Path $taskOutput ("book$($taskIndex+1).json")
    $taskMatrices+=$taskMatrix
    & (Join-Path $taskRoot 'tools/compare_android_pipeline.ps1') -Source $taskSources[$taskIndex] -Output $taskMatrix
    if ($LASTEXITCODE -ne 0) {throw "第 $($taskIndex+1) 部样本消融未完成；查看 $taskMatrix 与当前批次状态。"}
}
$taskRuntime=Get-Content -Raw -Encoding UTF8 -LiteralPath 'config/runtime.json' | ConvertFrom-Json
$taskPython=$taskRuntime.python
& $taskPython -B .\tools\summarize_android_ablation.py @taskMatrices --output (Join-Path $taskOutput 'summary.json')
if ($LASTEXITCODE -ne 0) {throw '两部样本已运行，但汇总失败；原始矩阵仍保留。'}
Write-Host "消融汇总：$(Join-Path $taskOutput 'summary.json')"
