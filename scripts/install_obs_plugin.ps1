[CmdletBinding()]
param(
    [string]$BuildOutput,
    [string]$ObsInstallRoot = 'C:\Program Files\obs-studio',
    [string]$PluginRoot = 'C:\ProgramData\obs-studio\plugins'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$obsExe = Join-Path $ObsInstallRoot 'bin\64bit\obs64.exe'
if (-not (Test-Path -LiteralPath $obsExe -PathType Leaf)) {
    throw "Normale OBS-Installation wurde nicht gefunden: $obsExe"
}
$obsVersion = (Get-Item -LiteralPath $obsExe).VersionInfo.ProductVersion
if ($obsVersion -ne '32.1.2') {
    throw "Erwartet wird OBS 32.1.2, gefunden wurde '$obsVersion' unter $obsExe"
}
if (Get-Process -Name 'obs64' -ErrorAction SilentlyContinue) {
    throw 'OBS läuft. Bitte OBS schließen und die Plugininstallation erneut ausführen.'
}

if ([string]::IsNullOrWhiteSpace($BuildOutput)) {
    $candidates = @(
        (Join-Path $repoRoot 'build\obs-nmake-cmake440\matrix-auto-cutter-obs.dll'),
        (Join-Path $repoRoot 'build\obs-nmake\matrix-auto-cutter-obs.dll')
    )
    $BuildOutput = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
        Select-Object -First 1
}
if ([string]::IsNullOrWhiteSpace($BuildOutput)) {
    throw 'Build-Output matrix-auto-cutter-obs.dll fehlt. Plugin zuerst mit CMake bauen.'
}
$source = (Resolve-Path -LiteralPath $BuildOutput).Path
$sourceItem = Get-Item -LiteralPath $source
if ($sourceItem.Length -lt 4096 -or $sourceItem.Extension -ne '.dll') {
    throw "Build-Output ist keine plausible Plugin-DLL: $source"
}

$targetDirectory = Join-Path $PluginRoot 'matrix-auto-cutter-obs\bin\64bit'
$target = Join-Path $targetDirectory 'matrix-auto-cutter-obs.dll'
$localeSource = Join-Path $repoRoot 'resources\obs-plugin\locale\en-US.ini'
$localeDirectory = Join-Path $PluginRoot 'matrix-auto-cutter-obs\data\locale'
$localeTarget = Join-Path $localeDirectory 'en-US.ini'
if (-not (Test-Path -LiteralPath $localeSource -PathType Leaf)) {
    throw "Plugin-Locale-Datei fehlt: $localeSource"
}
[System.IO.Directory]::CreateDirectory($targetDirectory) | Out-Null
[System.IO.Directory]::CreateDirectory($localeDirectory) | Out-Null
$temporary = Join-Path $targetDirectory ('.matrix-auto-cutter-obs.' + [Guid]::NewGuid() + '.new')
$localeTemporary = Join-Path $localeDirectory ('.en-US.' + [Guid]::NewGuid() + '.new')
try {
    [System.IO.File]::Copy($source, $temporary, $false)
    [System.IO.File]::Copy($localeSource, $localeTemporary, $false)
    $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    $temporaryHash = (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash
    if ($sourceHash -ne $temporaryHash) {
        throw 'Hashprüfung der kopierten Plugin-DLL ist fehlgeschlagen.'
    }
    if ([System.IO.File]::Exists($localeTarget)) {
        [System.IO.File]::Replace($localeTemporary, $localeTarget, $null, $true)
    } else {
        [System.IO.File]::Move($localeTemporary, $localeTarget)
    }
    if ([System.IO.File]::Exists($target)) {
        [System.IO.File]::Replace($temporary, $target, $null, $true)
    } else {
        [System.IO.File]::Move($temporary, $target)
    }
} finally {
    if ([System.IO.File]::Exists($temporary)) {
        [System.IO.File]::Delete($temporary)
    }
    if ([System.IO.File]::Exists($localeTemporary)) {
        [System.IO.File]::Delete($localeTemporary)
    }
}

$installedHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
if ($installedHash -ne $sourceHash) {
    throw 'Abschließende Hashprüfung der installierten Plugin-DLL ist fehlgeschlagen.'
}
Write-Host "Plugin für die normale OBS-Installation installiert: $target"
Write-Host "Plugin-Daten: $localeTarget"
Write-Host "OBS: $obsExe (Version $obsVersion)"
Write-Host "SHA-256: $installedHash"
