# 本机服务启动器；后台窗口隐藏，日志位于本项目临时目录。
param([switch]$Follow)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$runtime = Get-Content -Raw -Encoding UTF8 config/runtime.json | ConvertFrom-Json
$taskReaderConfig = Get-Content -Raw -Encoding UTF8 config/reader.json | ConvertFrom-Json
$taskReaderPort = [int]$taskReaderConfig.reader_port
if ($taskReaderPort -lt 1 -or $taskReaderPort -gt 65535) {throw 'reader_port must be between 1 and 65535.'}
$taskReaderHealthUrl = 'http://127.0.0.1:{0}/health' -f $taskReaderPort
$taskReaderValidationUrl = 'http://127.0.0.1:{0}/models/validation/start' -f $taskReaderPort
$taskPython = [string]$runtime.python
if ($taskPython -and ![IO.Path]::IsPathRooted($taskPython)) {
 $taskPython = Join-Path $taskRoot $taskPython
}
if (!$taskPython -or !(Test-Path -LiteralPath $taskPython -PathType Leaf)) {
 [Console]::Error.WriteLine("运行时 Python 不存在：$taskPython。请修正 config/runtime.json 的 python 路径，或按 README 准备运行环境。")
 exit 1
}
$taskLogs = Join-Path $taskRoot '~temp/logs'
New-Item -ItemType Directory -Force -Path $taskLogs | Out-Null
$env:PYTHONUTF8='1'
$env:PYTHONUNBUFFERED='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:HF_HOME=Join-Path $taskRoot '~temp/huggingface'
$env:HF_HUB_OFFLINE='1'
$env:PATH=(Split-Path -Parent $taskPython)+';'+(Join-Path (Split-Path -Parent $taskPython) 'Scripts')+';'+$env:PATH
. (Join-Path $PSScriptRoot 'network_info.ps1')
function Test-ReaderEndpoint($url) {try { $null=Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 2; return $true } catch {return $false}}
$taskRecords=@()
$taskPidFile=Join-Path $taskLogs 'processes.json'
if (Test-Path -LiteralPath $taskPidFile) {
 $taskLoaded=Get-Content -Raw -Encoding UTF8 $taskPidFile | ConvertFrom-Json
 foreach ($record in $taskLoaded) {
  if ($record.id -and $record.started -and $record.path) {$taskRecords += $record}
 }
}
function Record-ReaderProcess($process) {
 $script:taskRecords+=@{id=$process.Id;started=$process.StartTime.ToUniversalTime().ToString('o');path=$taskPython}
 ConvertTo-Json -InputObject @($script:taskRecords) | Set-Content -Encoding UTF8 -LiteralPath $taskPidFile
}
function Assert-ReaderPortOwner($port) {
 foreach ($taskOwnerPid in @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)) {
  $taskOwner=Get-Process -Id $taskOwnerPid -ErrorAction SilentlyContinue
  $taskKnown=$false
  if ($taskOwner -and $taskOwner.Path) {
   foreach ($record in $taskRecords) {
    if ([int]$record.id -ne $taskOwnerPid) {continue}
    $taskStarted=[datetime]::Parse([string]$record.started,[Globalization.CultureInfo]::InvariantCulture,[Globalization.DateTimeStyles]::RoundtripKind)
    if ([IO.Path]::GetFullPath($taskOwner.Path) -eq [IO.Path]::GetFullPath([string]$record.path) -and
        $taskOwner.StartTime.ToUniversalTime().Ticks -eq $taskStarted.ToUniversalTime().Ticks) {$taskKnown=$true;break}
   }
  }
  if (!$taskKnown) {throw "端口 $port 被未由本启动器记录的进程占用（PID $taskOwnerPid），未接管。"}
 }
}
Assert-ReaderPortOwner 9882
Assert-ReaderPortOwner $taskReaderPort
if (!(Test-ReaderEndpoint 'http://127.0.0.1:9882/openapi.json')) {
 & $taskPython -B tools/prepare_tts.py
 if ($LASTEXITCODE -ne 0) {throw 'GPT-SoVITS 运行视图准备失败'}
 $env:PYTHONPATH=''
 $taskProcess=Start-Process -PassThru -FilePath $taskPython -ArgumentList @('-u','-B','api_v2.py','-a','127.0.0.1','-p','9882','-c',('"'+$taskRoot+'/config/tts.yaml"')) -WorkingDirectory ($taskRoot+'/~temp/gpt-sovits') -WindowStyle Hidden -RedirectStandardOutput ($taskLogs+'/tts.out.log') -RedirectStandardError ($taskLogs+'/tts.err.log')
 Record-ReaderProcess $taskProcess
}
if (!(Test-ReaderEndpoint $taskReaderHealthUrl)) {
 $env:PYTHONPATH=$taskRoot+'/~temp/ocrdeps;'+$taskRoot
 $taskProcess=Start-Process -PassThru -FilePath $taskPython -ArgumentList @('-u','-B','-m','uvicorn','server.app:app','--host','0.0.0.0','--port',[string]$taskReaderPort) -WorkingDirectory $taskRoot -WindowStyle Hidden -RedirectStandardOutput ($taskLogs+'/reader.out.log') -RedirectStandardError ($taskLogs+'/reader.err.log')
 Record-ReaderProcess $taskProcess
}
Show-ReaderPhoneConnectionHelp -ProjectRoot $taskRoot -Port $taskReaderPort
Write-Host ('安卓模拟器电脑地址： http://10.0.2.2:{0}' -f $taskReaderPort)
Write-Host '首次模型加载请稍等。'

$taskReady=$false
for ($attempt=0;$attempt -lt 30;$attempt++) {
 if ((Test-ReaderEndpoint $taskReaderHealthUrl) -and (Test-ReaderEndpoint 'http://127.0.0.1:9882/openapi.json')) {$taskReady=$true;break}
 Start-Sleep -Seconds 1
}
if (!$taskReady) {throw '服务未就绪，请查看 ~temp/logs 下的错误日志'}
if ($taskReaderConfig.startup_warmup -ne $false) {
 Write-Host '正在预热 OCR、TTS 和 ASR；首次启动可能需要约一分钟。'
 & $taskPython -u -B (Join-Path $taskRoot 'tools/warmup.py')
 if ($LASTEXITCODE -ne 0) {throw '启动预热失败；服务仍在运行，请查看 ~temp/logs'}
} else {
 Write-Host '启动预热已关闭；首次朗读将包含模型加载等待。'
}
$taskValidation=Invoke-RestMethod -Method Post -Uri $taskReaderValidationUrl -Headers @{'X-Reader-Token'=[string]$taskReaderConfig.token} -TimeoutSec 15
Write-Host '电脑已开始后台逐项验证未验证或上次失败的朗读模型；手机模型目录会显示进度。'
if ($Follow) {
    $taskRuntime=Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $taskRoot 'config/runtime.json') | ConvertFrom-Json
    $env:PYTHONUTF8='1'
    & $taskRuntime.python -u -B (Join-Path $taskRoot 'tools/live_debug.py') pc
    if ($LASTEXITCODE -ne 0) {throw '实时监视器异常退出；后台服务仍独立运行。'}
}
