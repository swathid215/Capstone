$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$python = Join-Path $backend '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    throw "Backend virtual environment not found: $python"
}
if (-not (Test-Path (Join-Path $frontend 'package.json'))) {
    throw "Frontend package.json not found: $frontend"
}

$backendProcess = Start-Process -FilePath $python `
    -WorkingDirectory $backend `
    -ArgumentList '-m', 'uvicorn', 'api.server:app', '--port', '8000' `
    -PassThru

$frontendProcess = Start-Process -FilePath 'npm.cmd' `
    -WorkingDirectory $frontend `
    -ArgumentList 'run', 'dev' `
    -PassThru

Write-Host "Backend:  http://127.0.0.1:8000"
Write-Host "Frontend: http://127.0.0.1:5173"
Write-Host 'Press Ctrl+C to stop both services.'

try {
    Wait-Process -Id $backendProcess.Id, $frontendProcess.Id
}
finally {
    foreach ($process in @($backendProcess, $frontendProcess)) {
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
