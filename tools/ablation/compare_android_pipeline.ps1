# Repeat the official ReaderAosp35 MediaProjection flow for comparable modes.
param(
    [ValidateSet('off','on','parallel','eager','prefetch','adaptive','history')][string[]]$Modes=@('off','parallel','eager','off','prefetch','adaptive','off','history','on'),
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Output
)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location -LiteralPath $taskRoot
$taskRuntime=Get-Content -Raw -Encoding UTF8 -LiteralPath 'config/runtime.json' | ConvertFrom-Json
$taskPython=$taskRuntime.python
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH="$taskRoot/~temp/ocrdeps;$taskRoot"
$env:HF_HUB_OFFLINE='1'
$taskOutput=[IO.Path]::GetFullPath($Output)
if (!$taskOutput.StartsWith([IO.Path]::GetFullPath((Join-Path $taskRoot '~outputs-intermediate/evidence')),[StringComparison]::OrdinalIgnoreCase)) {throw '结果文件必须位于项目的中间证据目录。'}
New-Item -ItemType Directory -Path (Split-Path -Parent $taskOutput) -Force | Out-Null
$taskActivePath=Join-Path $taskRoot '~temp/reproduction/active.json'
if (Test-Path -LiteralPath $taskActivePath) {
    $taskPrevious=Get-Content -Raw -Encoding UTF8 -LiteralPath $taskActivePath | ConvertFrom-Json
    if (!$taskPrevious.restored) {throw "批次 $($taskPrevious.id) 尚未恢复；先运行 tools/test_session.ps1 -Action restore。"}
}
$taskResults=@()
if (Test-Path -LiteralPath $taskOutput) {$taskResults=@(Get-Content -Raw -Encoding UTF8 -LiteralPath $taskOutput | ConvertFrom-Json)}

function Invoke-Checked([string]$label,[scriptblock]$operation) {
    Write-Host "[$label]" -ForegroundColor Cyan
    & $operation
    if ($LASTEXITCODE -ne 0) {throw "$label 失败；请按当前批次证据恢复环境。"}
}

for ($taskRound=0;$taskRound -lt $Modes.Count;$taskRound++) {
    $taskMode=$Modes[$taskRound]
    if (@($taskResults | Where-Object {$_.index -eq $taskRound -and $_.status -eq 'complete'}).Count) {continue}
    Write-Host "Android 消融：$taskMode 第 $($taskRound+1)/$($Modes.Count) 轮" -ForegroundColor Green
    $taskBatch=''
    $taskFailure=''
    try {
        Invoke-Checked 'new' {& .\tools\test_session.ps1 -Action new -Source $Source}
        $taskActive=Get-Content -Raw -Encoding UTF8 -LiteralPath '~temp/reproduction/active.json' | ConvertFrom-Json
        $taskBatch=$taskActive.id
        Invoke-Checked 'prepare' {& $taskPython -B .\tools\reproduce_test.py prepare}
        Invoke-Checked 'setup' {& $taskPython -B .\tools\reproduce_test.py setup --optimizations $taskMode}
        Invoke-Checked 'collect' {& $taskPython -B .\tools\reproduce_test.py collect}
        $taskRun=Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $taskActive.out 'run.json') | ConvertFrom-Json
        if (!$taskRun.finished) {throw '采集未覆盖全部样本页。'}
        Invoke-Checked 'gaps' {& $taskPython -B .\tools\analyze_playback_gaps.py (Join-Path $taskActive.out 'android.log')}
        $taskGaps=Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $taskActive.out 'playback-gaps.json') | ConvertFrom-Json
        $taskExpected=0
        foreach ($taskPage in $taskRun.records) {$taskExpected += [Math]::Max(0,[int]$taskPage.units-1)}
        if (@($taskGaps.intervals).Count -ne $taskExpected -or @($taskGaps.intervals | Where-Object {$_.status -ne 'ok'}).Count) {throw '逐单元边界或 WAV 有声位置不完整，不能计入消融结果。'}
        Invoke-Checked 'report' {& $taskPython -B .\tools\reproduce_test.py report}
    } catch {
        $taskFailure=$_.Exception.Message
        Write-Host "本轮失败：$taskFailure" -ForegroundColor Red
    } finally {
        if (!$taskBatch -and (Test-Path -LiteralPath $taskActivePath)) {
            $taskCandidate=Get-Content -Raw -Encoding UTF8 -LiteralPath $taskActivePath | ConvertFrom-Json
            if (!$taskCandidate.restored) {$taskBatch=$taskCandidate.id}
        }
        if ($taskBatch) {
            try {Invoke-Checked 'restore' {& .\tools\test_session.ps1 -Action restore}}
            catch {throw "恢复失败，停止矩阵：$($_.Exception.Message)"}
        }
    }
    $taskResults=@($taskResults | Where-Object {$_.index -ne $taskRound})
    $taskResults += [pscustomobject]@{index=$taskRound;mode=$taskMode;source=$Source;batch=$taskBatch;status=$(if ($taskFailure) {'failed'} else {'complete'});error=$taskFailure}
    $taskResults | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 -LiteralPath $taskOutput
}
$taskResults | Format-Table -AutoSize
if (@($taskResults | Where-Object {$_.status -ne 'complete'}).Count) {throw '消融矩阵含失败批次；修复后原命令可跳过已完成项续跑。'}
