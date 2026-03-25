param(
  [string]$InputPath = "data/sample/features.json",
  [string]$Strategy = "demo",
  [int]$Seed = 20260325,
  [string]$PythonExe = "D:\qt\.python\python.exe",
  [string]$DeliveryRoot = "delivery",
  [string]$RunName = "demo-run"
)

$scriptRoot = $PSScriptRoot
$projectRoot = (Resolve-Path (Join-Path $scriptRoot "..")).Path
$repoRoot = (Resolve-Path (Join-Path $projectRoot "..")).Path

if ([System.IO.Path]::IsPathRooted($DeliveryRoot)) {
  $deliveryBase = $DeliveryRoot
} else {
  $deliveryBase = (Join-Path $repoRoot $DeliveryRoot)
}

$runDir = Join-Path $deliveryBase $RunName
$zipPath = Join-Path $deliveryBase "$RunName.zip"

Write-Host "==> Step 1/3: Run signal demo"
powershell -ExecutionPolicy Bypass -File (Join-Path $scriptRoot "run_demo.ps1") -InputPath $InputPath -Strategy $Strategy -PythonExe $PythonExe
if ($LASTEXITCODE -ne 0) { throw "run_demo.ps1 failed" }

Write-Host "==> Step 2/3: Generate evaluation artifacts"
powershell -ExecutionPolicy Bypass -File (Join-Path $scriptRoot "run_eval.ps1") -InputPath $InputPath -Strategy $Strategy -OutputDir $runDir -Seed $Seed -PythonExe $PythonExe
if ($LASTEXITCODE -ne 0) { throw "run_eval.ps1 failed" }

Write-Host "==> Step 3/3: Package delivery zip"
if (Test-Path $zipPath) {
  Remove-Item $zipPath -Force
}
Compress-Archive -Path (Join-Path $runDir "*") -DestinationPath $zipPath -CompressionLevel Optimal

Write-Host ""
Write-Host "Done."
Write-Host "Artifacts folder: $runDir"
Write-Host "Zip package: $zipPath"
