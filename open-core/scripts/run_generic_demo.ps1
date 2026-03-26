param(
  [string]$InputPath = "data/sample/generic_eval.json",
  [string]$OutputDir = "..\\delivery\\generic-demo",
  [int]$Seed = 20260325,
  [string]$PythonExe = ""
)

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
  if (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonExe = (Get-Command python).Source
  } elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonExe = (Get-Command py).Source
  } elseif (Test-Path "D:\qt\.python\python.exe") {
    $PythonExe = "D:\qt\.python\python.exe"
  } else {
    throw "Python executable not found. Set -PythonExe explicitly."
  }
}

if (-not (Test-Path $PythonExe)) {
  throw "Python executable not found: $PythonExe"
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([System.IO.Path]::IsPathRooted($InputPath)) {
  $resolvedInput = $InputPath
} else {
  $resolvedInput = (Resolve-Path (Join-Path $projectRoot $InputPath)).Path
}

if ([System.IO.Path]::IsPathRooted($OutputDir)) {
  $resolvedOutput = $OutputDir
} else {
  $resolvedOutput = (Join-Path $projectRoot $OutputDir)
}

& $PythonExe -c "import sys; sys.path.insert(0, r'$projectRoot'); from src.generic_eval_cli import main; main()" --input $resolvedInput --out-dir $resolvedOutput --seed $Seed
