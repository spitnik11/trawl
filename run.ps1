# Debug launcher - shows the console and all backend output.
# For normal use, double-click the Trawl desktop shortcut instead.
param([switch]$FetchOnly)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command py -ErrorAction SilentlyContinue).Source }
if (-not $py) { throw "Python not found on PATH. Install Python 3.10+ and retry." }

if (-not (Test-Path ".\config.json")) {
    Copy-Item ".\config.example.json" ".\config.json"
    Write-Host "Created config.json. Hacker News works now; add Reddit keys when you want them:" -ForegroundColor Yellow
    Write-Host "  https://www.reddit.com/prefs/apps  ->  create a 'script' app`n" -ForegroundColor Yellow
}

if ($FetchOnly) { & $py trawl.py fetch } else { & $py trawl.py serve }
