param(
  [string]$InputPath = "data/sample/features.json",
  [string]$Strategy = "demo",
  [string]$OutputDir = "..\\delivery\\demo-run",
  [int]$Seed = 20260325,
  [string]$PythonExe = "D:\qt\.python\python.exe"
)

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

& $PythonExe -c "import sys; sys.path.insert(0, r'$projectRoot'); from src.eval_cli import main; main()" --input $resolvedInput --strategy $Strategy --out-dir $resolvedOutput --seed $Seed

