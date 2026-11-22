# Fallback used by make_app.bat when Inno Setup is not installed: copies the app to a normal place
# and adds Start menu + desktop shortcuts. Per-user, no administrator rights.
param([Parameter(Mandatory = $true)][string]$Exe)
$target = Join-Path $env:LOCALAPPDATA "Programs\Laptop AI"
New-Item -ItemType Directory -Force $target | Out-Null
Copy-Item $Exe (Join-Path $target "LaptopAI.exe") -Force
$shell = New-Object -ComObject WScript.Shell
$places = @(
    (Join-Path ([Environment]::GetFolderPath("Programs")) "Laptop AI.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Laptop AI.lnk")
)
foreach ($p in $places) {
    $s = $shell.CreateShortcut($p)
    $s.TargetPath = Join-Path $target "LaptopAI.exe"
    $s.WorkingDirectory = $target
    $s.Description = "Laptop AI"
    $s.Save()
}
Write-Host "Installed to $target with Start menu and desktop shortcuts."
