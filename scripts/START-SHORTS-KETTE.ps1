[CmdletBinding()]
param(
    # Alle drei nur fuer den Handbetrieb. Die Aufgabenplanung ruft das Skript
    # ohne Parameter auf -- ohne --aufnahme waehlt die Kette selbst die
    # juengste unverfallene Aufnahme (kette.py bestimme_aufnahme).
    [string]$Aufnahme,
    [switch]$Trocken,
    [string]$Modell
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Fester Pfad statt $PSScriptRoot\.. -- Absicht, kein Versehen.
#
# Die Windows-Aufgabenplanung startet eine Aufgabe mit einem UNBESTIMMTEN
# Arbeitsverzeichnis (in der Praxis meist C:\Windows\system32, je nach
# Anmeldeart aber auch anders). Und TREFFERQUOTE_PFAD in
# src\matrix_auto_cutter\shorts\auswahl.py ist RELATIV
# ('labels/repeat/trefferquote.json'). Aus dem falschen Verzeichnis heraus
# legt die Kette diese Datei also an der falschen Stelle an, statt zu
# scheitern -- ein stiller Fehler, den man erst Tage spaeter bemerkt.
# Darum wird hier hart und nachpruefbar ins Repo gewechselt.
# ---------------------------------------------------------------------------
$RepoWurzel = 'P:\DimensionWithin-MatrixMarketAutoEditor'

if (-not (Test-Path -LiteralPath $RepoWurzel)) {
    Write-Host "ANGEHALTEN: Repo-Wurzel $RepoWurzel nicht erreichbar."
    exit 1
}
Set-Location -LiteralPath $RepoWurzel

# ---------------------------------------------------------------------------
# Protokoll. kette.py schreibt selbst keines, sondern nur auf stdout -- das
# Umlenken ist vollstaendig Sache dieses Skripts. Jede Zeile bekommt einen
# Zeitstempel, weil die Stufen zwischen Sekunden und einer halben Stunde
# dauern und man hinterher wissen will, wo die Zeit blieb.
# ---------------------------------------------------------------------------
$Start = Get-Date
$ProtokollOrdner = Join-Path $RepoWurzel 'artefakte\repeat\kette-protokoll'
if (-not (Test-Path -LiteralPath $ProtokollOrdner)) {
    New-Item -ItemType Directory -Path $ProtokollOrdner -Force | Out-Null
}
$Protokoll = Join-Path $ProtokollOrdner ($Start.ToString('yyyy-MM-dd-HHmmss') + '.log')

function Schreibe-Zeile {
    param([string]$Text)
    $zeile = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Text
    Write-Host $zeile
    Add-Content -LiteralPath $Protokoll -Value $zeile -Encoding utf8
}

# ---------------------------------------------------------------------------
# Aufruf zusammensetzen. OHNE --aufnahme, solange der Nutzer keinen nennt.
# ---------------------------------------------------------------------------
$Argumente = @('run', 'python', '-m', 'matrix_auto_cutter.shorts.kette')
if ($Aufnahme) { $Argumente += @('--aufnahme', $Aufnahme) }
if ($Trocken)  { $Argumente += '--trocken' }
if ($Modell)   { $Argumente += @('--modell', $Modell) }

Schreibe-Zeile "Startskript in $RepoWurzel"
Schreibe-Zeile ('Aufruf: uv ' + ($Argumente -join ' '))
if (-not $Aufnahme) {
    Schreibe-Zeile 'Ohne --aufnahme: die Kette waehlt die juengste unverfallene Aufnahme selbst.'
}

# ---------------------------------------------------------------------------
# Lauf. Die Ausgabe geht Zeile fuer Zeile durch Schreibe-Zeile, damit sie
# gleichzeitig im Protokoll und auf stdout steht. 2>&1 holt auch stderr.
# Die gewaehlte Aufnahme steht in der Kettenausgabe ("  Aufnahme:      NAME")
# -- sie wird beim Durchreichen mitgelesen, statt sie ein zweites Mal
# eigenstaendig zu bestimmen. Zwei Bestimmungswege koennten auseinanderlaufen.
# ---------------------------------------------------------------------------
$GewaehlteAufnahme = '(keine)'
$KeineArbeit = $false

# ErrorActionPreference waehrend des Laufs gesenkt: in Windows PowerShell 5.1
# verpackt 2>&1 jede stderr-Zeile eines fremden Programms in einen
# ErrorRecord. Bei 'Stop' wuerde schon eine harmlose Warnung von uv oder
# ffmpeg das Skript abbrechen, bevor die Kette fertig ist.
$VorigeFehlerart = $ErrorActionPreference
$ErrorActionPreference = 'Continue'

& uv @Argumente 2>&1 | ForEach-Object {
    $text = [string]$_
    # (.+?) statt (\S+): Aufnahmenamen enthalten ein Leerzeichen
    # ("2026-08-25 15-14-00"), \S+ wuerde sie hinter dem Datum abschneiden.
    if ($text -match '^\s*Aufnahme:\s+(.+?)\s*$') { $GewaehlteAufnahme = $Matches[1] }
    if ($text -match 'ANGEHALTEN \[(keine_aufnahme|nur_verfallen)\]') { $KeineArbeit = $true }
    Schreibe-Zeile $text
}
$Rueckgabecode = $LASTEXITCODE
$ErrorActionPreference = $VorigeFehlerart

# ---------------------------------------------------------------------------
# "Nichts zu tun" ist kein Fehlschlag.
#
# kette.py vergibt CODE_KEINE_AUFNAHME = 2 (kette.py Zeile 89) an genau zwei
# Stellen, beide in bestimme_aufnahme: gar keine Aufnahme im Bestand
# (Zeile 365) und nur eine verfallene, aelter als VERFALL_STUNDEN = 48
# (Zeile 372). Sonst wird die 2 in kette.py nirgends vergeben; echte
# Fehlschlaege tragen 5, 9 oder 10. Der Fall ist also am Code allein sauber
# erkennbar. Der Marker ANGEHALTEN [keine_aufnahme|nur_verfallen] wird
# trotzdem mitgelesen und muss uebereinstimmen -- faellt kette.py je
# auseinander, sieht man es hier statt es zu verschlucken.
#
# An einem Tag ohne Aufnahme ist das der Normalfall, kein Grund fuer die
# Aufgabenplanung, einen Fehlschlag zu melden. Darum wird 2 auf 0 gesenkt.
# Jeder ANDERE von null verschiedene Code wird unveraendert weitergereicht.
# ---------------------------------------------------------------------------
$Gemeldet = $Rueckgabecode
if ($Rueckgabecode -eq 2) {
    if ($KeineArbeit) {
        Schreibe-Zeile 'Nichts zu tun: keine unverfallene Aufnahme vorhanden. Das ist der Normalfall an einem Tag ohne Aufnahme, kein Fehlschlag.'
    } else {
        Schreibe-Zeile 'Rueckgabecode 2 ohne den erwarteten Marker ANGEHALTEN [keine_aufnahme|nur_verfallen] -- trotzdem als "nichts zu tun" gewertet, aber nachsehen lohnt.'
    }
    $Gemeldet = 0
}

$Ende = Get-Date
$Dauer = $Ende - $Start
Schreibe-Zeile ('ZUSAMMENFASSUNG Start {0} Ende {1} Dauer {2} Rueckgabecode {3} (gemeldet {4}) Aufnahme {5}' -f `
    $Start.ToString('yyyy-MM-dd HH:mm:ss'), `
    $Ende.ToString('yyyy-MM-dd HH:mm:ss'), `
    $Dauer.ToString('hh\:mm\:ss'), `
    $Rueckgabecode, `
    $Gemeldet, `
    $GewaehlteAufnahme)

# Die Kette endet bei den Kandidaten. Urteilen, buendeln zur Auswahl, bauen
# und hochladen bleibt beim Menschen -- dieses Skript ruft weder urteilslauf
# noch einen Bau auf.
exit $Gemeldet
