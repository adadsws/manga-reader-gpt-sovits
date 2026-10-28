# 自动测试维护入口；兼容 Windows PowerShell 5，不依赖当前工作目录。
param(
    [Parameter(Mandatory=$true)][string]$Action,
    [string]$Source='',
    [ValidateSet('keep','on','off','parallel','eager','prefetch','adaptive','history')][string]$Optimizations='keep'
)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskRuntime=Get-Content -Raw -Encoding UTF8 -LiteralPath 'config/runtime.json' | ConvertFrom-Json
$taskPython=$taskRuntime.python
$taskAdb=Join-Path $taskRoot '~temp/android-sdk/platform-tools/adb.exe'
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH="$taskRoot/~temp/ocrdeps;$taskRoot"
$env:HF_HUB_OFFLINE='1'
function Invoke-ReaderStep([string]$step) {
    $taskArguments=@('-B',(Join-Path $taskRoot 'tools/reproduce_test.py'),$step)
    if ($Source -and $step -in @('check','new')) {$taskArguments+=@('--source',$Source)}
    if ($step -eq 'setup') {$taskArguments+=@('--optimizations',$Optimizations)}
    & $taskPython @taskArguments
    if ($LASTEXITCODE -ne 0) {throw "步骤未完成：$step。请查看上方原因；已有文件会保留。"}
}
function Restart-Reader {
    $taskListener=Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($taskListener) {
        $taskProcess=Get-Process -Id $taskListener.OwningProcess
        $taskRecords=Get-Content -Raw -Encoding UTF8 -LiteralPath '~temp/logs/processes.json' | ConvertFrom-Json
        $taskRecord=$taskRecords | Where-Object id -eq $taskProcess.Id | Select-Object -Last 1
        if (!$taskRecord) {throw '8765端口不是本启动器已记录的进程，请先检查占用。'}
        $taskRecordTime=if ($taskRecord.started -is [datetime]) {$taskRecord.started} else {[datetime]::Parse([string]$taskRecord.started,[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::RoundtripKind)}
        if ([IO.Path]::GetFullPath($taskProcess.Path) -ne [IO.Path]::GetFullPath($taskRecord.path) -or $taskProcess.StartTime.ToUniversalTime().Ticks -ne $taskRecordTime.ToUniversalTime().Ticks) {throw '进程身份不匹配，未停止该进程。'}
        Stop-Process -Id $taskProcess.Id
        $null=$taskProcess.WaitForExit(10000)
    }
    for ($i=0;$i -lt 30;$i++) {
        if (!(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)) {break}
        Start-Sleep -Seconds 1
    }
    if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) {throw '8765端口尚未释放。'}
    Start-Sleep -Seconds 3
    try { & (Join-Path $taskRoot 'tools/start.ps1') }
    catch {
        Write-Host '首次启动未就绪，等待端口/模型加载后重试一次。'
        Start-Sleep -Seconds 8
        & (Join-Path $taskRoot 'tools/start.ps1')
    }
}
function Start-ReaderEmulator {
    # 复用已验证的可见启动入口，由它按 AVD 名识别唯一设备并校正窗口。
    $env:READER_NO_PAUSE='1'
    & (Join-Path $taskRoot 'tools/start_emulator.ps1')
    if ($LASTEXITCODE -ne 0) {throw '专用 ReaderAosp35 启动或 APK 安装失败。'}
    $taskSerial=(& $taskPython -B -c 'from tools.test_android import find_reader_serial; print(find_reader_serial())' | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or !$taskSerial) {throw '无法唯一识别 ReaderAosp35。'}
    # 正式 APK 重装可能让系统清空已授权服务；仅在已核验的专用 AVD 中恢复本项目固定服务。
    & $taskAdb -s $taskSerial shell settings put secure enabled_accessibility_services org.local.reader/.TurnService
    & $taskAdb -s $taskSerial shell settings put secure accessibility_enabled 1
}
try {
    switch ($Action) {
        'new' {Invoke-ReaderStep 'new'; Restart-Reader; Start-ReaderEmulator}
        'restore' {Invoke-ReaderStep 'restore-files'; Restart-Reader; Invoke-ReaderStep 'restore-android'}
        default {Invoke-ReaderStep $Action}
    }
    exit 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host '如已运行01，请在排查后运行 tools/test_session.ps1 -Action restore 恢复原配置。'
    exit 1
}
