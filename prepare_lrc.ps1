param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ToolArgs = @()
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = ''
$EnvDir = Join-Path $PSScriptRoot '.lrc-env'
$VenvPython = Join-Path $EnvDir 'Scripts\python.exe'
$ManagedPython = Join-Path $env:LOCALAPPDATA 'Programs\Pebby-Lyrics\Python314\python.exe'
$Requirements = Join-Path $PSScriptRoot 'requirements-lyrics.txt'

function Confirm-Install([string]$Message) {
    $answer = [string](Read-Host "$Message [Y/N，默认 N]")
    return $answer.Trim() -ieq 'Y'
}

function Test-Python($Candidate) {
    if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate.Path -PathType Leaf)) { return $false }
    try {
        $prefix = @($Candidate.Prefix)
        & $Candidate.Path @prefix -I -X utf8 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

function Find-Python {
    # 新变量名优先，同时接受旧名，方便从旧版工具迁移。
    $override = $env:PEBBY_LYRICS_PYTHON
    if (-not $override) { $override = $env:PEBBY_LRC_PYTHON }
    if ($override) {
        $candidate = @{ Path = $override; Prefix = @() }
        if (Test-Python $candidate) { return $candidate }
        throw 'PEBBY_LYRICS_PYTHON 指定的解释器不可用，要求 Python 3.10+。'
    }
    foreach ($path in @($VenvPython, $ManagedPython)) {
        $candidate = @{ Path = $path; Prefix = @() }
        if (Test-Python $candidate) { return $candidate }
    }
    foreach ($name in @('py', 'python', 'python3')) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        # 跳过会弹出 Microsoft Store 的占位命令。
        if (-not $command -or $command.Source -like '*\Microsoft\WindowsApps\*') { continue }
        $prefix = @()
        if ($name -eq 'py') { $prefix = @('-3') }
        $candidate = @{ Path = $command.Source; Prefix = $prefix }
        if (Test-Python $candidate) { return $candidate }
    }
    # 商店版 Python 只能通过 WindowsApps 别名启动（实体 exe 无执行权限）。
    # 仅在确认已安装对应包后探测，避免未安装时误开 Microsoft Store。
    $storeAlias = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\python.exe'
    if (Test-Path -LiteralPath $storeAlias -PathType Leaf) {
        $storePackages = @(Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps') -Directory -ErrorAction SilentlyContinue |
                           Where-Object { $_.Name -like 'PythonSoftwareFoundation.Python.*' })
        if ($storePackages.Count -gt 0) {
            $candidate = @{ Path = $storeAlias; Prefix = @() }
            if (Test-Python $candidate) { return $candidate }
        }
    }
    foreach ($registry in @('HKCU:\Software\Python\PythonCore', 'HKLM:\Software\Python\PythonCore', 'HKLM:\Software\WOW6432Node\Python\PythonCore')) {
        foreach ($key in @(Get-ChildItem -LiteralPath $registry -ErrorAction SilentlyContinue)) {
            $install = Get-ItemProperty -LiteralPath ($key.PSPath + '\InstallPath') -ErrorAction SilentlyContinue
            if ($install -and $install.'(default)') {
                $candidate = @{ Path = (Join-Path $install.'(default)' 'python.exe'); Prefix = @() }
                if (Test-Python $candidate) { return $candidate }
            }
        }
    }
    return $null
}

function Install-Python {
    $architecture = $env:PROCESSOR_ARCHITEW6432
    if (-not $architecture) { $architecture = $env:PROCESSOR_ARCHITECTURE }
    if ($architecture -ne 'AMD64') {
        throw '自动安装仅支持 Windows x64；请从 python.org 安装适合本机的 Python 3.10+ 后重试。'
    }
    Write-Host '未找到 Python 3.10+。可下载 Python 官方 3.14.10 x64 安装器。'
    Write-Host ('仅安装到当前用户目录：{0}' -f (Split-Path -Parent $ManagedPython))
    Write-Host '不要求管理员权限，不修改 PATH，不设置文件关联。'
    if (-not (Confirm-Install '是否下载并安装 Python')) { return $null }
    $downloadDir = Join-Path $EnvDir 'downloads'
    New-Item -ItemType Directory -Path $downloadDir -Force | Out-Null
    $installer = Join-Path $downloadDir 'python-3.14.10-amd64.exe'
    $sha256 = '92480239264744fe0386a1bd8275786fefa126e3129e56a68e22b52c73329965'
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    if (-not (Test-Path -LiteralPath $installer) -or (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash -ine $sha256) {
        Write-Host '从 python.org 下载中，请稍候……'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.14.10/python-3.14.10-amd64.exe' -OutFile $installer -TimeoutSec 180
    }
    if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash -ine $sha256) { throw 'Python 安装器 SHA-256 不匹配，已停止。' }
    $signature = Get-AuthenticodeSignature -LiteralPath $installer
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
        throw '无法确认 Python 安装器的官方有效签名，已停止；不会关闭签名校验。'
    }
    $target = Split-Path -Parent $ManagedPython
    $arguments = @('/quiet', '/norestart', 'InstallAllUsers=0', 'Include_pip=1', 'Include_launcher=0',
                   'InstallLauncherAllUsers=0', 'Include_test=0', 'AssociateFiles=0', 'Shortcuts=0',
                   'PrependPath=0', ('TargetDir="{0}"' -f $target))
    Write-Host '正在为当前用户安装 Python……'
    $process = Start-Process -FilePath $installer -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -notin @(0, 3010)) { throw "Python 安装失败，退出码 $($process.ExitCode)。" }
    if ($process.ExitCode -eq 3010) { Write-Host '安装器提示需要重启；若后续解释器检查失败，请重启后再运行。' }
    $candidate = @{ Path = $ManagedPython; Prefix = @() }
    if (-not (Test-Python $candidate)) { throw '安装后仍无法运行 Python，请检查安装日志或手动安装。' }
    return $candidate
}

function Test-Dependencies {
    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) { return $false }
    $check = @"
from importlib.metadata import version
from mutagen import File
from opencc import OpenCC
assert (1,47) <= tuple(map(int,version('mutagen').split('.')[:2])) < (2,0)
assert (0,1,7) <= tuple(map(int,version('opencc-python-reimplemented').split('.')[:3])) < (0,2,0)
OpenCC('t2s')
"@
    try {
        & $VenvPython -I -X utf8 -c $check 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

try {
    Write-Host '=== Pebby Lyrics 歌词工具（命令行） ==='
    if (-not (Test-Path -LiteralPath $Requirements -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'prepare_lrc.py') -PathType Leaf)) {
        throw '请把 prepare_lrc.cmd、prepare_lrc.ps1、prepare_lrc.py、requirements-lyrics.txt 放在同一个目录。'
    }
    $python = Find-Python
    if (-not $python) { $python = Install-Python }
    if (-not $python) {
        Write-Host '未安装 Python，已退出。没有改动音频或歌词。'
        exit 3
    }
    if (-not (Test-Dependencies)) {
        Write-Host '缺少或需要补齐依赖：Mutagen（音频标签）、OpenCC（简繁比较/可选转换）。'
        Write-Host ("将在 {0} 中创建/补齐隔离环境，不向全局 Python 安装包。" -f $EnvDir)
        if (-not (Confirm-Install '是否联网安装这些依赖')) {
            Write-Host '未安装依赖，已退出。没有改动音频或歌词。'
            exit 3
        }
        $tempDir = Join-Path $EnvDir 'tmp'
        New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
        $env:TEMP = $tempDir
        $env:TMP = $tempDir
        if (-not (Test-Python @{ Path = $VenvPython; Prefix = @() })) {
            $prefix = @($python.Prefix)
            & $python.Path @prefix -I -X utf8 -m venv $EnvDir
            if ($LASTEXITCODE -ne 0) { throw '创建隔离环境失败，请检查 Python 的 venv/pip 组件。' }
        }
        Write-Host '正在联网安装依赖（单次连接超时 20 秒，最多重试 1 次）……'
        & $VenvPython -I -X utf8 -m pip install --disable-pip-version-check --require-virtualenv --no-cache-dir --no-input --timeout 20 --retries 1 --progress-bar off --log (Join-Path $EnvDir 'install.log') -r $Requirements
        if ($LASTEXITCODE -ne 0 -or -not (Test-Dependencies)) { throw '依赖安装失败，请检查网络/代理后重新运行；不会跳过证书校验。' }
    }
    $arguments = @($ToolArgs)
    if ($arguments.Count -eq 0) {
        $defaultRoot = Join-Path ([Environment]::GetFolderPath('Desktop')) '音乐'
        $inputRoot = Read-Host ("音乐目录（回车使用 {0}）" -f $defaultRoot)
        $inputRoot = $inputRoot.Trim().Trim('"')
        if (-not $inputRoot) { $inputRoot = $defaultRoot }
        $arguments = @($inputRoot)
        Write-Host '默认 QQ 音乐优先、LRCLIB 兜底；优先简体来源，找不到时用繁体；不强制转换。'
        Write-Host '默认移除片头信息，长句按 18 字拆分，已有 LRC 跳过。'
        $prefer = [string](Read-Host '优先简体还是繁体？输入 S 或 T（默认 S）')
        if ($prefer.Trim() -ieq 'T') {
            $arguments += @('--prefer', 'traditional')
        } else {
            $arguments += @('--prefer', 'simplified')
        }
    }
    $hasReport = @($arguments | Where-Object { $_ -eq '--report' -or $_ -like '--report=*' }).Count -gt 0
    if (-not $hasReport -and '-h' -notin $arguments -and '--help' -notin $arguments) {
        $reportDir = Join-Path $EnvDir 'reports'
        New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
        $arguments += @('--report', (Join-Path $reportDir ('lrc-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '.json')))
    }
    & $VenvPython -I -X utf8 (Join-Path $PSScriptRoot 'prepare_lrc.py') @arguments
    exit $LASTEXITCODE
} catch {
    Write-Host ("错误：{0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 2
}
