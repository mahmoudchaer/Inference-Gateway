$ErrorActionPreference = "Stop"

$Repository = "mahmoudchaer/Inference-Gateway"
$BaseUrl = "https://github.com/$Repository/releases/latest/download"
$Artifact = "inference-gateway-windows-x64.exe"
$InstallDir = if ($env:INFERENCE_GATEWAY_INSTALL_DIR) { $env:INFERENCE_GATEWAY_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "InferenceGateway\bin" }
$Temporary = Join-Path ([System.IO.Path]::GetTempPath()) ("inference-gateway-" + [guid]::NewGuid())

try {
    New-Item -ItemType Directory -Force -Path $Temporary | Out-Null
    Write-Host "Downloading Inference Gateway..."
    Invoke-WebRequest "$BaseUrl/$Artifact" -OutFile (Join-Path $Temporary $Artifact)
    Invoke-WebRequest "$BaseUrl/checksums.txt" -OutFile (Join-Path $Temporary "checksums.txt")

    $ChecksumLine = Get-Content (Join-Path $Temporary "checksums.txt") | Where-Object { $_ -match "\s+$([regex]::Escape($Artifact))$" } | Select-Object -First 1
    if (-not $ChecksumLine) { throw "The release checksum is missing." }
    $Expected = ($ChecksumLine -split "\s+")[0].ToLowerInvariant()
    $Actual = (Get-FileHash (Join-Path $Temporary $Artifact) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Expected -ne $Actual) { throw "Checksum verification failed; nothing was installed." }

    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Move-Item -Force (Join-Path $Temporary $Artifact) (Join-Path $InstallDir "inference-gateway.exe")

    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $Parts = @($UserPath -split ";" | Where-Object { $_ })
    if ($Parts -notcontains $InstallDir) {
        $NewPath = (($Parts + $InstallDir) -join ";")
        [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
        $env:Path = "$env:Path;$InstallDir"
        Write-Host "Added $InstallDir to your PATH."
    }
    Write-Host ""
    Write-Host "Installed Inference Gateway."
    Write-Host ""
    Write-Host "Next steps:"
    Write-Host "  1. Set your API key:  inference-gateway config --api-key YOUR_JEV_KEY"
    Write-Host "  2. Start the gateway:  inference-gateway start"
    Write-Host "  3. See all commands:   inference-gateway help"
}
finally {
    if (Test-Path $Temporary) { Remove-Item -Recurse -Force $Temporary }
}
