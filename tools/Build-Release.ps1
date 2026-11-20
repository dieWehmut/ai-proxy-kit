#Requires -Version 5.1
[CmdletBinding()]
param([ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+$')][string]$Version = '1.0.1')

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$dist = Join-Path $root 'dist'
$null = New-Item -ItemType Directory -Path $dist -Force
$files = @('install-sub2api.cmd', 'install-sub2api.ps1', 'start-sub2api.cmd', 'start-sub2api.ps1',
    'stop-sub2api.cmd', 'stop-sub2api.ps1', 'run-sub2api.ps1', 'lib\Sub2API.Windows.ps1',
    'services\docker-compose.yml', 'assets\downloads.json', 'README.md', 'LICENSE',
    '.gitignore', '.gitattributes', 'tests\Test-Functions.ps1', 'tests\Test-Installation.ps1',
    'tools\Build-Release.ps1', 'install.sh', 'start.sh', 'stop.sh', 'status.sh', 'check.sh',
    'connect.sh', 'install.cmd', 'start.cmd', 'stop.cmd', 'status.cmd', 'check.cmd',
    'proxykit.ps1', 'compose.yml', 'compose.workbuddy-proxy.yml', '.env.example',
    'lib\proxykit.py', 'scripts\connect-sub2api.py', 'tools\build-workbuddy-proxy.py',
    'tests\test_proxykit.py', 'docs\SUB2API_WINDOWS.md', 'docs\AISTUDIO_COMPARISON.md',
    'docs\VALIDATION.md', 'docs\AISTUDIO_LOCAL_LOGIN.md', 'scripts\import-aistudio-session.py',
    'tools\google-session-extension\manifest.json', 'tools\google-session-extension\background.js',
    'tools\google-session-extension\export.html', 'tools\google-session-extension\export.mjs',
    'tests\test_aistudio_session.py', 'tests\test_google_session_export.mjs')
Add-Type -AssemblyName System.IO.Compression
$archive = Join-Path $dist "ai-proxy-kit-$Version.zip"
$stream = [IO.File]::Open($archive, [IO.FileMode]::Create)
$zip = New-Object IO.Compression.ZipArchive($stream, [IO.Compression.ZipArchiveMode]::Create, $false)
try {
    foreach ($file in $files) {
        # ZIP uses forward slashes even when it is created on Windows.
        $entry = $zip.CreateEntry($file.Replace('\', '/'), [IO.Compression.CompressionLevel]::Optimal)
        $source = [IO.File]::OpenRead((Join-Path $root $file))
        $destination = $entry.Open()
        try { $source.CopyTo($destination) }
        finally { $destination.Dispose(); $source.Dispose() }
    }
} finally { $zip.Dispose(); $stream.Dispose() }
$checksum = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText(($archive + '.sha256'), ($checksum + '  ' + [IO.Path]::GetFileName($archive) + "`n"), [Text.Encoding]::ASCII)
Write-Host "Public script package: $archive"
Write-Host "SHA256: $checksum"
