$ErrorActionPreference = "Stop"

$veado = "Q:\Veadotube\veadotube-mini.exe"
$obs   = "C:\Program Files\obs-studio\bin\64bit\obs64.exe"
$start = "P:\DimensionWithin-MatrixMarketAutoEditor\START-MATRIX-AUTO-CUTTER.cmd"

function Warte-AufFenster($name, $sekunden) {
    $ende = (Get-Date).AddSeconds($sekunden)
    while ((Get-Date) -lt $ende) {
        $p = Get-Process | Where-Object {
            $_.ProcessName -match $name -and $_.MainWindowHandle -ne 0
        } | Select-Object -First 1
        if ($p) { return $p }
        Start-Sleep -Milliseconds 500
    }
    return $null
}

if (-not (Get-Process -Name "veadotube-mini" -ErrorAction SilentlyContinue)) {
    Write-Host "Starte Veadotube..."
    Start-Process $veado
} else {
    Write-Host "Veadotube laeuft bereits."
}
$pVeado = Warte-AufFenster "veadotube" 30
if (-not $pVeado) { Write-Host "WARNUNG: Veadotube-Fenster nicht gefunden." -ForegroundColor Yellow }

if (-not (Get-Process -Name "obs64" -ErrorAction SilentlyContinue)) {
    Write-Host "Starte OBS..."
    Start-Process -FilePath $obs -WorkingDirectory (Split-Path $obs)
} else {
    Write-Host "OBS laeuft bereits."
}
$pObs = Warte-AufFenster "obs64" 60
if (-not $pObs) { Write-Host "WARNUNG: OBS-Fenster nicht gefunden." -ForegroundColor Yellow }
Write-Host "Warte, bis OBS die Quellen gebunden hat..."
Start-Sleep -Seconds 8

if ($pVeado) {
    Write-Host "Hole Veadotube kurz in den Fokus..."
    $wsh = New-Object -ComObject WScript.Shell
    $wsh.AppActivate($pVeado.Id) | Out-Null
    Start-Sleep -Seconds 2
    if ($pObs) { $wsh.AppActivate($pObs.Id) | Out-Null }
}

Write-Host "Starte Runner..."
& cmd.exe /c "`"$start`""

Write-Host ""
Write-Host "Fertig. Pruefe, ob dein Avatar in OBS sichtbar ist." -ForegroundColor Cyan
