# Trawl launcher — starts the hidden backend and opens the app window.
# Not meant to be run directly from a shortcut; use Trawl.vbs so no console appears.
param([switch]$Silent, [switch]$Web)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

function Fail($msg) {
    if ($Silent) {
        (New-Object -ComObject WScript.Shell).Popup($msg, 0, 'Trawl', 16) | Out-Null
    } else {
        Write-Host $msg -ForegroundColor Red
    }
    exit 1
}

# --- python (pythonw has no console window at all) ---
$pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
$py  = (Get-Command python  -ErrorAction SilentlyContinue).Source
if (-not $pyw -and $py) { $pyw = Join-Path (Split-Path $py) 'pythonw.exe' }
if (-not $pyw -or -not (Test-Path $pyw)) {
    Fail "Python not found.`n`nInstall Python 3.10+ and make sure it's on PATH, then try again."
}

# --- port / config ---
$port = 8420
if (Test-Path .\config.json) {
    try { $port = [int](Get-Content .\config.json -Raw | ConvertFrom-Json).server.port } catch {}
} else {
    Copy-Item .\config.example.json .\config.json
}

# Probe by CONNECTING, never by binding: a bind to 127.0.0.1 succeeds while another
# socket holds the port on the wildcard address, which would start a second server.
function Test-Port([int]$p) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $ok = $c.ConnectAsync('127.0.0.1', $p).Wait(250)
        $c.Close()
        return $ok
    } catch { return $false }
}

if (Test-Port $port) {
    # already running - just open a window at it
    $already = $true
} else {
    $already = $false
    Start-Process -FilePath $pyw -ArgumentList 'trawl.py','app' -WorkingDirectory $PSScriptRoot | Out-Null
}

# --- wait for it to answer ---
$ready = $false
foreach ($i in 1..40) {
    if (Test-Port $port) { $ready = $true; break }
    Start-Sleep -Milliseconds 250
}
if (-not $ready) {
    Fail "Trawl's backend didn't start.`n`nTry running this from a terminal to see the error:`n  cd `"$PSScriptRoot`"`n  python trawl.py app"
}

$url = "http://localhost:$port"

if ($Web) { Start-Process $url; exit 0 }

# --- open as a NORMAL Chrome browser tab (uses your real Chrome profile) ---
# Deliberately not --app mode: the user wants a plain tab in their own Chrome window.
# Closing the tab stops the /api/ping heartbeat and trawl.py self-exits after IDLE_SHUTDOWN_S.
$chrome = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($chrome) {
    Start-Process -FilePath $chrome -ArgumentList $url | Out-Null
} else {
    Start-Process $url   # no Chrome -> whatever the default browser is, still a normal tab
}

# Nothing to clean up: closing the tab stops the heartbeat and trawl.py exits on its own.
