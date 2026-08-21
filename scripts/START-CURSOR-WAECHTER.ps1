[CmdletBinding()]
param(
    [string]$TargetDir = 'F:\ShortsQuellen\Cursor\',
    [int]$SampleIntervalMs = 100,
    [int]$FlushIntervalMs = 1000,
    [string]$ObsHost = '127.0.0.1',
    [int]$ObsPort = 4455,
    [int]$ReconnectDelaySeconds = 5,

    # Probelaeufe -- keine dieser Optionen verbindet sich zu OBS ausser -ProbeConnect / -ProbeReconnect.
    [switch]$ProbeLog,
    [int]$ProbeLogSeconds = 10,
    [switch]$ProbeConnect,
    [switch]$ProbeReconnect,
    [int]$ProbeReconnectPort = 0,
    [int]$ProbeReconnectSeconds = 12
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Einzelinstanz-Sperre -- benannter Mutex statt Get-CimInstance Win32_Process
# (das scheitert auf manchen Rechnern mit "Ungueltige Klasse"). Faellt beim
# Prozessende von selbst, analog zu ShortsSingleInstance/JudgeServerSingleInstance
# in src\matrix_auto_cutter\shorts\ (dort per Sperrdatei statt Mutex geloest).
# Gilt in JEDEM Fall, auch bei den Probelaeufen und bei manuellem Start.
# ---------------------------------------------------------------------------

$waechterMutex = New-Object System.Threading.Mutex($false, 'MatrixAutoCutter-CursorWaechter')
if (-not $waechterMutex.WaitOne(0)) {
    Write-Host 'Cursor-Waechter laeuft bereits fuer diesen Benutzer -- beende mich.'
    exit 3
}

# ---------------------------------------------------------------------------
# Cursor-Sampler: eine kompilierte C#-Klasse mit eigenem echten .NET-Thread.
# Laeuft unabhaengig vom WebSocket-Empfang im Hauptthread weiter. Steuerung
# ausschliesslich ueber eine threadsichere Befehls-Queue (Start/Stop).
# Grund fuer C# statt PowerShell-Runspace: ein zweites [powershell]-Objekt,
# per BeginInvoke gestartet, blockierte in Tests zuverlaessig -- ein echter
# System.Threading.Thread mit reinem .NET-Code tut das nicht.
# ---------------------------------------------------------------------------

$samplerSource = @'
using System;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading;
using System.Windows.Forms;

public class CursorSampler
{
    private class Cmd
    {
        public string Type;
        public string TargetDir;
        public DateTime At;
        public string OutputPath;
    }

    private static readonly UTF8Encoding Utf8NoBom = new UTF8Encoding(false);

    private readonly ConcurrentQueue<Cmd> _commands = new ConcurrentQueue<Cmd>();
    private readonly int _sampleIntervalMs;
    private readonly int _flushIntervalMs;
    private Thread _thread;
    private volatile bool _stop;

    public volatile int Rows;
    public string LastCsvPath;
    public string LastJsonPath;

    public CursorSampler(int sampleIntervalMs, int flushIntervalMs)
    {
        _sampleIntervalMs = sampleIntervalMs;
        _flushIntervalMs = flushIntervalMs;
    }

    public void Run()
    {
        _thread = new Thread(Loop);
        _thread.IsBackground = true;
        _thread.Start();
    }

    public void StartLog(string targetDir, DateTime at)
    {
        _commands.Enqueue(new Cmd { Type = "start", TargetDir = targetDir, At = at });
    }

    public void StopLog(DateTime at, string outputPath)
    {
        _commands.Enqueue(new Cmd { Type = "stop", At = at, OutputPath = outputPath });
    }

    public void Shutdown()
    {
        _stop = true;
        if (_thread != null) _thread.Join(5000);
    }

    private void Loop()
    {
        StreamWriter writer = null;
        string csvPath = null;
        string jsonPath = null;
        DateTime recordingStartedAt = default(DateTime);
        string obsOutputPath = null;
        DateTime? csvFirstRowAt = null;
        DateTime? previousSampleAt = null;
        double sampleDeltaSumMs = 0;
        int sampleDeltaCount = 0;
        int rows = 0;
        var lastFlush = Stopwatch.StartNew();
        var lastSample = Stopwatch.StartNew();
        int pollMs = Math.Min(20, _sampleIntervalMs);
        if (pollMs < 1) pollMs = 1;

        try
        {
            while (!_stop)
            {
                Cmd cmd;
                while (_commands.TryDequeue(out cmd))
                {
                    if (cmd.Type == "start")
                    {
                        if (writer != null) continue;
                        Directory.CreateDirectory(cmd.TargetDir);
                        string stamp = cmd.At.ToString("yyyy-MM-dd HH-mm-ss");
                        csvPath = Path.Combine(cmd.TargetDir, "cursor-" + stamp + ".csv");
                        jsonPath = Path.Combine(cmd.TargetDir, "cursor-" + stamp + ".json");
                        LastCsvPath = csvPath;
                        LastJsonPath = jsonPath;
                        recordingStartedAt = cmd.At;
                        obsOutputPath = null;
                        csvFirstRowAt = null;
                        previousSampleAt = null;
                        sampleDeltaSumMs = 0;
                        sampleDeltaCount = 0;
                        rows = 0;
                        Rows = 0;
                        writer = new StreamWriter(csvPath, false, Utf8NoBom);
                        writer.NewLine = "\r\n";
                        writer.AutoFlush = false;
                        writer.WriteLine("zeit,x,y");
                        writer.Flush();
                        lastFlush.Restart();
                        lastSample.Restart();
                    }
                    else if (cmd.Type == "stop")
                    {
                        if (cmd.OutputPath != null) obsOutputPath = cmd.OutputPath;
                        CloseCurrent(ref writer, jsonPath, recordingStartedAt, cmd.At, csvFirstRowAt, rows, _sampleIntervalMs, obsOutputPath, sampleDeltaSumMs, sampleDeltaCount);
                    }
                }

                if (writer != null && lastSample.ElapsedMilliseconds >= _sampleIntervalMs)
                {
                    lastSample.Restart();
                    var p = Cursor.Position;
                    var now = DateTime.Now;
                    if (csvFirstRowAt == null) csvFirstRowAt = now;
                    if (previousSampleAt != null)
                    {
                        sampleDeltaSumMs += (now - previousSampleAt.Value).TotalMilliseconds;
                        sampleDeltaCount++;
                    }
                    previousSampleAt = now;
                    writer.WriteLine(now.ToString("o", CultureInfo.InvariantCulture) + "," + p.X + "," + p.Y);
                    rows++;
                    Rows = rows;
                    if (lastFlush.ElapsedMilliseconds >= _flushIntervalMs)
                    {
                        writer.Flush();
                        lastFlush.Restart();
                    }
                }

                Thread.Sleep(pollMs);
            }
        }
        finally
        {
            if (writer != null)
            {
                CloseCurrent(ref writer, jsonPath, recordingStartedAt, DateTime.Now, csvFirstRowAt, rows, _sampleIntervalMs, obsOutputPath, sampleDeltaSumMs, sampleDeltaCount);
            }
        }
    }

    private static void CloseCurrent(ref StreamWriter writer, string jsonPath, DateTime startedAt, DateTime stoppedAt, DateTime? firstRowAt, int rows, int sampleIntervalMs, string outputPath, double sampleDeltaSumMs, int sampleDeltaCount)
    {
        if (writer == null) return;
        writer.Flush();
        writer.Close();
        writer = null;

        double? lead = firstRowAt.HasValue ? (double?)(startedAt - firstRowAt.Value).TotalSeconds : null;
        double? measuredIntervalMs = sampleDeltaCount > 0 ? (double?)(sampleDeltaSumMs / sampleDeltaCount) : null;
        string json = "{\n" +
            "  \"recording_started_at\": " + JsonStr(startedAt.ToString("o", CultureInfo.InvariantCulture)) + ",\n" +
            "  \"recording_stopped_at\": " + JsonStr(stoppedAt.ToString("o", CultureInfo.InvariantCulture)) + ",\n" +
            "  \"csv_first_row_at\": " + (firstRowAt.HasValue ? JsonStr(firstRowAt.Value.ToString("o", CultureInfo.InvariantCulture)) : "null") + ",\n" +
            "  \"lead_seconds\": " + (lead.HasValue ? lead.Value.ToString(CultureInfo.InvariantCulture) : "null") + ",\n" +
            "  \"rows\": " + rows + ",\n" +
            "  \"sample_interval_ms\": " + sampleIntervalMs + ",\n" +
            "  \"sample_interval_measured_ms\": " + (measuredIntervalMs.HasValue ? measuredIntervalMs.Value.ToString(CultureInfo.InvariantCulture) : "null") + ",\n" +
            "  \"obs_output_path\": " + (outputPath != null ? JsonStr(outputPath) : "null") + "\n" +
            "}\n";
        string tmp = jsonPath + ".tmp-" + Guid.NewGuid().ToString("N");
        File.WriteAllText(tmp, json, new UTF8Encoding(false));
        if (File.Exists(jsonPath))
        {
            File.Replace(tmp, jsonPath, null);
        }
        else
        {
            File.Move(tmp, jsonPath);
        }
    }

    private static string JsonStr(string s)
    {
        return "\"" + s.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    }
}
'@

Add-Type -TypeDefinition $samplerSource -ReferencedAssemblies System.Windows.Forms, System.Drawing

function Start-CursorLog {
    param($Sampler, [string]$TargetDir, [datetime]$At)
    $Sampler.StartLog($TargetDir, $At)
}

function Stop-CursorLog {
    param($Sampler, [datetime]$At, $OutputPath)
    $Sampler.StopLog($At, $OutputPath)
}

# ---------------------------------------------------------------------------
# obs-websocket v5: Passwort lesen, Auth berechnen (nur im Speicher, nie
# ausgegeben), Hello/Identify durchfuehren.
# ---------------------------------------------------------------------------

function Get-ObsWebsocketPassword {
    $configPath = Join-Path $env:APPDATA 'obs-studio\plugin_config\obs-websocket\config.json'
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
        throw "obs-websocket-Konfiguration nicht gefunden: $configPath"
    }
    $cfg = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    return $cfg.server_password
}

function Get-Sha256Base64 {
    param([string]$Text)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        $hash = $sha.ComputeHash($bytes)
        return [Convert]::ToBase64String($hash)
    } finally {
        $sha.Dispose()
    }
}

function Get-ObsAuthResponse {
    param([string]$Password, [string]$Salt, [string]$Challenge)
    $secret = Get-Sha256Base64 -Text ($Password + $Salt)
    return Get-Sha256Base64 -Text ($secret + $Challenge)
}

function Connect-ObsWebSocket {
    param([string]$ObsHost, [int]$ObsPort)
    $uri = [Uri]"ws://${ObsHost}:${ObsPort}"
    $ws = [System.Net.WebSockets.ClientWebSocket]::new()
    $cts = [System.Threading.CancellationTokenSource]::new()
    $ws.ConnectAsync($uri, $cts.Token).GetAwaiter().GetResult() | Out-Null
    return [pscustomobject]@{ Socket = $ws; Cts = $cts }
}

function Receive-ObsMessage {
    param($Conn)
    $buffer = New-Object byte[] 16384
    $segment = [System.ArraySegment[byte]]::new($buffer)
    $ms = New-Object System.IO.MemoryStream
    do {
        $result = $Conn.Socket.ReceiveAsync($segment, $Conn.Cts.Token).GetAwaiter().GetResult()
        if ($result.MessageType -eq [System.Net.WebSockets.WebSocketMessageType]::Close) {
            return $null
        }
        $ms.Write($buffer, 0, $result.Count)
    } while (-not $result.EndOfMessage)
    $text = [System.Text.Encoding]::UTF8.GetString($ms.ToArray())
    return $text | ConvertFrom-Json
}

function Send-ObsMessage {
    param($Conn, $Payload)
    $json = $Payload | ConvertTo-Json -Depth 8 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $segment = [System.ArraySegment[byte]]::new($bytes)
    $Conn.Socket.SendAsync($segment, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $Conn.Cts.Token).GetAwaiter().GetResult() | Out-Null
}

function Invoke-ObsIdentify {
    param($Conn)
    $hello = Receive-ObsMessage -Conn $Conn
    if ($null -eq $hello -or $hello.op -ne 0) {
        throw 'Kein gueltiges Hello von obs-websocket erhalten.'
    }
    $identify = @{ op = 1; d = @{ rpcVersion = 1 } }
    if ($null -ne $hello.d.authentication) {
        $password = Get-ObsWebsocketPassword
        $auth = Get-ObsAuthResponse -Password $password -Salt $hello.d.authentication.salt -Challenge $hello.d.authentication.challenge
        $password = $null
        $identify.d.authentication = $auth
    }
    Send-ObsMessage -Conn $Conn -Payload $identify
    $identified = Receive-ObsMessage -Conn $Conn
    if ($null -eq $identified -or $identified.op -ne 2) {
        throw 'Identify wurde von obs-websocket nicht bestaetigt.'
    }
    return $identified
}

# ---------------------------------------------------------------------------
# Probelauf 1: nur der Protokollteil, ohne OBS.
# ---------------------------------------------------------------------------

function Invoke-ProbeLog {
    param([string]$TargetDir, [int]$Seconds, [int]$SampleIntervalMs, [int]$FlushIntervalMs)

    Write-Host "Probelauf 1: Protokoll allein, $Seconds Sekunden, Ziel: $TargetDir"
    $sampler = New-Object CursorSampler ($SampleIntervalMs, $FlushIntervalMs)
    $sampler.Run()
    $startedAt = Get-Date
    Start-CursorLog -Sampler $sampler -TargetDir $TargetDir -At $startedAt
    Start-Sleep -Seconds $Seconds
    $stoppedAt = Get-Date
    Stop-CursorLog -Sampler $sampler -At $stoppedAt -OutputPath $null
    Start-Sleep -Milliseconds 500
    $sampler.Shutdown()

    $csvPath = $sampler.LastCsvPath
    $jsonPath = $sampler.LastJsonPath

    $lines = Get-Content -LiteralPath $csvPath
    $dataLines = $lines | Select-Object -Skip 1
    $timestamps = $dataLines | ForEach-Object { [datetime]([string]$_).Split(',')[0] }
    $deltas = @()
    for ($i = 1; $i -lt $timestamps.Count; $i++) {
        $deltas += ($timestamps[$i] - $timestamps[$i - 1]).TotalMilliseconds
    }
    $size = (Get-Item -LiteralPath $csvPath).Length

    [pscustomobject]@{
        CsvPath      = $csvPath
        JsonPath     = $jsonPath
        Header       = $lines[0]
        FirstThree   = $dataLines | Select-Object -First 3
        LastThree    = $dataLines | Select-Object -Last 3
        RowCount     = $dataLines.Count
        MeanDeltaMs  = if ($deltas.Count -gt 0) { ($deltas | Measure-Object -Average).Average } else { $null }
        MinDeltaMs   = if ($deltas.Count -gt 0) { ($deltas | Measure-Object -Minimum).Minimum } else { $null }
        MaxDeltaMs   = if ($deltas.Count -gt 0) { ($deltas | Measure-Object -Maximum).Maximum } else { $null }
        SizeBytes    = $size
        Sidecar      = Get-Content -LiteralPath $jsonPath -Raw
    }
}

# ---------------------------------------------------------------------------
# Probelauf 2: Verbindungs- und Anmeldetest, einzige Anfrage GetVersion.
# ---------------------------------------------------------------------------

function Invoke-ProbeConnect {
    param([string]$ObsHost, [int]$ObsPort)

    Write-Host "Probelauf 2: Verbindungstest gegen ${ObsHost}:${ObsPort}"
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $connectTask = $tcp.ConnectAsync($ObsHost, $ObsPort)
        if (-not $connectTask.Wait(2000)) {
            $tcp.Close()
            Write-Host 'OBS laeuft nicht bzw. obs-websocket ist nicht erreichbar -- Probelauf 2 wird uebersprungen.'
            return $null
        }
        $tcp.Close()
    } catch {
        Write-Host 'OBS laeuft nicht bzw. obs-websocket ist nicht erreichbar -- Probelauf 2 wird uebersprungen.'
        return $null
    }

    $conn = Connect-ObsWebSocket -ObsHost $ObsHost -ObsPort $ObsPort
    try {
        Invoke-ObsIdentify -Conn $conn | Out-Null
        $requestId = [Guid]::NewGuid().ToString()
        Send-ObsMessage -Conn $conn -Payload @{ op = 6; d = @{ requestType = 'GetVersion'; requestId = $requestId } }
        $response = Receive-ObsMessage -Conn $conn
        if ($response.op -ne 7 -or $response.d.requestId -ne $requestId) {
            throw 'Unerwartete Antwort auf GetVersion.'
        }
        $version = $response.d.responseData.obsWebSocketVersion
        Write-Host "obs-websocket-Version: $version"
        return $version
    } finally {
        try { $conn.Socket.CloseAsync([System.Net.WebSockets.WebSocketCloseStatus]::NormalClosure, '', $conn.Cts.Token).GetAwaiter().GetResult() | Out-Null } catch { }
        $conn.Socket.Dispose()
    }
}

# ---------------------------------------------------------------------------
# Probelauf 3: Wiederverbindung gegen einen unerreichbaren Port.
# ---------------------------------------------------------------------------

function Invoke-ProbeReconnect {
    param([string]$ObsHost, [int]$Port, [int]$Seconds, [int]$DelaySeconds)

    if ($Port -eq 0) {
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
        $listener.Start()
        $Port = $listener.LocalEndpoint.Port
        $listener.Stop()
    }

    Write-Host "Probelauf 3: Wiederverbindung gegen freien Port ${ObsHost}:${Port}, $Seconds Sekunden"
    $attempts = 0
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $Seconds) {
        $attempts++
        try {
            $conn = Connect-ObsWebSocket -ObsHost $ObsHost -ObsPort $Port
            Write-Host "Versuch $attempts unerwartet erfolgreich -- Port war nicht frei."
            $conn.Socket.Dispose()
            break
        } catch {
            Write-Host "Versuch $attempts fehlgeschlagen, naechster Versuch in $DelaySeconds s ($([Math]::Round($sw.Elapsed.TotalSeconds, 1)) s vergangen)."
        }
        Start-Sleep -Seconds $DelaySeconds
    }
    [pscustomobject]@{ Attempts = $attempts; ElapsedSeconds = $sw.Elapsed.TotalSeconds }
}

# ---------------------------------------------------------------------------
# Daemon-Betrieb: haengt endlos am WebSocket, startet/stoppt das Protokoll
# anhand von RecordStateChanged. Sendet ausser der Identify-Handshake keine
# steuernde Anfrage an OBS.
# ---------------------------------------------------------------------------

function Start-Watcher {
    param([string]$TargetDir, [string]$ObsHost, [int]$ObsPort, [int]$SampleIntervalMs, [int]$FlushIntervalMs, [int]$ReconnectDelaySeconds)

    $sampler = New-Object CursorSampler ($SampleIntervalMs, $FlushIntervalMs)
    $sampler.Run()
    $isRecording = $false
    $verbunden = $false
    $versucheOhneVerbindung = 0
    try {
        while ($true) {
            $conn = $null
            try {
                $conn = Connect-ObsWebSocket -ObsHost $ObsHost -ObsPort $ObsPort
                Invoke-ObsIdentify -Conn $conn | Out-Null
                if (-not $verbunden) {
                    if ($versucheOhneVerbindung -gt 0) { Write-Host '' }
                    Write-Host "Verbunden zu obs-websocket unter ${ObsHost}:${ObsPort}. Warte auf RecordStateChanged."
                    $verbunden = $true
                    $versucheOhneVerbindung = 0
                }

                while ($true) {
                    $msg = Receive-ObsMessage -Conn $conn
                    if ($null -eq $msg) { throw 'Verbindung wurde von OBS geschlossen.' }
                    if ($msg.op -ne 5) { continue }
                    if ($msg.d.eventType -ne 'RecordStateChanged') { continue }

                    $state = $msg.d.eventData.outputState
                    $now = Get-Date
                    if ($state -eq 'OBS_WEBSOCKET_OUTPUT_STARTING') {
                        if (-not $isRecording) {
                            Write-Host "Aufnahme startet -- Protokoll wird geoeffnet ($($now.ToString('o')))."
                            Start-CursorLog -Sampler $sampler -TargetDir $TargetDir -At $now
                            $isRecording = $true
                        }
                    } elseif ($state -eq 'OBS_WEBSOCKET_OUTPUT_STOPPED') {
                        if ($isRecording) {
                            $outputPath = $msg.d.eventData.outputPath
                            Write-Host "Aufnahme beendet -- Protokoll wird geschlossen ($($now.ToString('o')))."
                            Stop-CursorLog -Sampler $sampler -At $now -OutputPath $outputPath
                            $isRecording = $false
                        }
                    }
                }
            } catch {
                if ($verbunden) {
                    Write-Host "Verbindung zu OBS verloren -- versuche erneut alle $ReconnectDelaySeconds s."
                    $verbunden = $false
                } else {
                    Write-Host -NoNewline '.'
                    $versucheOhneVerbindung++
                }
                Start-Sleep -Seconds $ReconnectDelaySeconds
            } finally {
                if ($null -ne $conn) {
                    try { $conn.Socket.Dispose() } catch { }
                }
            }
        }
    } finally {
        if ($isRecording) {
            Stop-CursorLog -Sampler $sampler -At (Get-Date) -OutputPath $null
            Start-Sleep -Milliseconds 500
        }
        $sampler.Shutdown()
    }
}

# ---------------------------------------------------------------------------
# Einstieg
# ---------------------------------------------------------------------------

try {
    if ($ProbeLog) {
        Invoke-ProbeLog -TargetDir $TargetDir -Seconds $ProbeLogSeconds -SampleIntervalMs $SampleIntervalMs -FlushIntervalMs $FlushIntervalMs
    } elseif ($ProbeConnect) {
        Invoke-ProbeConnect -ObsHost $ObsHost -ObsPort $ObsPort
    } elseif ($ProbeReconnect) {
        Invoke-ProbeReconnect -ObsHost $ObsHost -Port $ProbeReconnectPort -Seconds $ProbeReconnectSeconds -DelaySeconds $ReconnectDelaySeconds
    } else {
        Start-Watcher -TargetDir $TargetDir -ObsHost $ObsHost -ObsPort $ObsPort -SampleIntervalMs $SampleIntervalMs -FlushIntervalMs $FlushIntervalMs -ReconnectDelaySeconds $ReconnectDelaySeconds
    }
} finally {
    $waechterMutex.ReleaseMutex()
    $waechterMutex.Dispose()
}
