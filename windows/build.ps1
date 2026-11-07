param([string]$Python = 'python', [string]$OutputDirectory = 'dist')

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Push-Location -LiteralPath $root
try {
    $output = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) { $OutputDirectory } else { Join-Path $root $OutputDirectory }
    New-Item -ItemType Directory -Force -Path $output | Out-Null
    & $Python -c 'from timecapsulesmb.windows_gui import configure_tcl; configure_tcl(); from PyInstaller.__main__ import run; run()' `
        --noconfirm --onedir --windowed --name TimeCapsuleSMB `
        --distpath $output --workpath (Join-Path $output '.pyinstaller') --specpath $output `
        --collect-data timecapsulesmb --collect-submodules zeroconf windows/launch.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
    $bundle = Join-Path $output 'TimeCapsuleSMB'
    $artifacts = Get-Content -Raw 'src/timecapsulesmb/assets/artifact-manifest.json' | ConvertFrom-Json
    foreach ($artifact in $artifacts.artifacts.PSObject.Properties.Value) {
        $destination = Join-Path $bundle $artifact.path
        New-Item -ItemType Directory -Force -Path (Split-Path $destination -Parent) | Out-Null
        Copy-Item -LiteralPath $artifact.path -Destination $destination
    }
    Copy-Item -LiteralPath LICENSE -Destination $bundle
    Copy-Item -LiteralPath windows/README.md -Destination (Join-Path $bundle 'README-Windows.md')
    $revision = & git rev-parse HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Could not resolve the source revision.' }
    "Corresponding source: https://github.com/Yukare1/TimeCapsuleSMB/tree/$revision" |
        Set-Content -Encoding utf8 (Join-Path $bundle 'SOURCE.txt')
    $archive = Join-Path $output 'TimeCapsuleSMB-Windows-x64.zip'
    Compress-Archive -Path $bundle -DestinationPath $archive -Force
    Write-Output $archive
}
finally {
    Pop-Location
}
