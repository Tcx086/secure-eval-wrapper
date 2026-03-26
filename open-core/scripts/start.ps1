param(
  [ValidateSet("all", "quant", "generic")]
  [string]$Mode = "all"
)

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

$pythonExe = $null
if (Get-Command python -ErrorAction SilentlyContinue) {
  $pythonExe = (Get-Command python).Source
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
  $pythonExe = (Get-Command py).Source
} elseif (Test-Path "D:\qt\.python\python.exe") {
  $pythonExe = "D:\qt\.python\python.exe"
}

if (-not $pythonExe) {
  throw "No Python executable found (python/py/.python). Install Python or configure PATH."
}

Write-Host "Using Python: $pythonExe"
& $pythonExe (Join-Path $projectRoot "main.py") --mode $Mode --python-exe $pythonExe
