[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$obsRoot = 'C:\Program Files\obs-studio'
$obsExe = Join-Path $obsRoot 'bin\64bit\obs64.exe'
$installedPlugin = 'C:\ProgramData\obs-studio\plugins\matrix-auto-cutter-obs\bin\64bit\matrix-auto-cutter-obs.dll'
$installedLocale = 'C:\ProgramData\obs-studio\plugins\matrix-auto-cutter-obs\data\locale\en-US.ini'
$sourceLocale = Join-Path $repoRoot 'resources\obs-plugin\locale\en-US.ini'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python-Umgebung fehlt: $python. Bitte zuerst 'uv sync --all-extras' ausführen."
}
if (-not (Test-Path -LiteralPath $obsExe -PathType Leaf)) {
    throw "Normale OBS-Installation fehlt: $obsExe"
}
$version = (Get-Item -LiteralPath $obsExe).VersionInfo.ProductVersion
if ($version -ne '32.1.2') {
    throw "Dieser Produktstart erwartet OBS 32.1.2; gefunden wurde '$version'."
}

$buildCandidates = @(
    (Join-Path $repoRoot 'build\obs-nmake-cmake440\matrix-auto-cutter-obs.dll'),
    (Join-Path $repoRoot 'build\obs-nmake\matrix-auto-cutter-obs.dll')
)
$buildPlugin = $buildCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1
$installationRequired = -not (Test-Path -LiteralPath $installedPlugin -PathType Leaf) -or
    -not (Test-Path -LiteralPath $installedLocale -PathType Leaf)
if (-not $installationRequired -and $buildPlugin) {
    $installationRequired = (Get-FileHash -LiteralPath $installedPlugin -Algorithm SHA256).Hash -ne
        (Get-FileHash -LiteralPath $buildPlugin -Algorithm SHA256).Hash
}
if (-not $installationRequired) {
    $installationRequired = (Get-FileHash -LiteralPath $installedLocale -Algorithm SHA256).Hash -ne
        (Get-FileHash -LiteralPath $sourceLocale -Algorithm SHA256).Hash
}
if ($installationRequired) {
    if (Get-Process -Name 'obs64' -ErrorAction SilentlyContinue) {
        throw 'Plugin fehlt oder ist veraltet, OBS läuft aber. OBS schließen und erneut starten.'
    }
    if (-not $buildPlugin) {
        throw 'Plugin fehlt und es wurde kein gebautes matrix-auto-cutter-obs.dll gefunden.'
    }
    & (Join-Path $PSScriptRoot 'install_obs_plugin.ps1') -BuildOutput $buildPlugin -ObsInstallRoot $obsRoot
}

$runner = Start-Process -FilePath $python -ArgumentList @(
    '-m', 'matrix_auto_cutter.product_runner'
) -WorkingDirectory $repoRoot -PassThru
Start-Sleep -Milliseconds 1500
if ($runner.HasExited) {
    if ($runner.ExitCode -eq 2) {
        Write-Host 'Product Runner läuft bereits; vorhandene Instanz wird verwendet.'
    } else {
        throw "Product Runner konnte nicht gestartet werden (Exitcode $($runner.ExitCode))."
    }
} else {
    Write-Host "Product Runner gestartet (PID $($runner.Id)); Statusfenster bleibt sichtbar."
}

$obs = Get-Process -Name 'obs64' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($obs) {
    Write-Host "Normales OBS 32.1.2 läuft bereits (PID $($obs.Id))."
} else {
    $obs = Start-Process -FilePath $obsExe -WorkingDirectory (Split-Path -Parent $obsExe) -PassThru
    Write-Host "Normales OBS 32.1.2 gestartet (PID $($obs.Id)): $obsExe"
}
