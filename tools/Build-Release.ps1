#Requires -Version 5.1
[CmdletBinding()]
param([ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+$')][string]$Version = '1.0.1')

$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
$dist = Join-Path $root 'dist'
$null = New-Item -ItemType Directory -Path $dist -Force
$files = @('install-sub2api.cmd', 'install-sub2api.ps1', 'start-sub2api.cmd', 'start-sub2api.ps1',
    'stop-sub2api.cmd', 'stop-sub2api.ps1', 'run-sub2api.ps1', 'lib\Sub2API.Windows.ps1',
    'services\docker-compose.yml', 'assets\downloads.json', 'README.md', 'LICENSE',
    '.gitignore', '.gitattributes', 'tests\Test-Functions.ps1', 'tests\Test-Installation.ps1',
    'tests\Test-Aistudio2ApiImport.ps1', 'tools\Build-Release.ps1',
    'install.sh', 'start.sh', 'stop.sh', 'status.sh', 'check.sh',
    'connect.sh', 'access.sh', 'install.cmd', 'start.cmd', 'stop.cmd', 'status.cmd', 'check.cmd',
    'open-aistudio.cmd', 'import-aistudio2api-session.cmd', 'import-aistudio2api-session.ps1',
    'proxykit.ps1', 'compose.yml', 'compose.workbuddy-proxy.yml', '.env.example',
    'lib\proxykit.py', 'scripts\connect-sub2api.py', 'tools\build-workbuddy-proxy.py',
    'tests\test_proxykit.py', 'tests\test_aistudio_compose.py', 'tests\test_sub2api_connection.py',
    'docs\SUB2API_WINDOWS.md', 'docs\AISTUDIO_COMPARISON.md', 'docs\AISTUDIO2API.md',
    'docs\AISTUDIO_LXC158.md', 'docs\VALIDATION.md', 'docs\AISTUDIO_LOCAL_LOGIN.md',
    'docs\WINDOWS_AISTUDIO_INSTALL.md', 'docs\validation\windows-aistudio-proxy-20261124.md',
    'docs\validation\aistudio-playground-migration-20261125.md',
    'tools\google-session-extension\manifest.json', 'tools\google-session-extension\background.js',
    'tools\google-session-extension\export.html', 'tools\google-session-extension\export.mjs',
    'tests\test_google_session_export.mjs')
$rootPrefix = $root.TrimEnd([char[]]@('\', '/')) + [IO.Path]::DirectorySeparatorChar
$validatedFiles = New-Object 'System.Collections.Generic.List[string]'
foreach ($file in $files) {
    $resolved = Resolve-Path -LiteralPath (Join-Path $root $file) -ErrorAction Stop
    if ($resolved.Provider.Name -ne 'FileSystem') { throw ('Release source is not a filesystem path: ' + $file) }
    $resolvedPath = [IO.Path]::GetFullPath([string]$resolved.Path)
    if (-not $resolvedPath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw ('Release source resolves outside the repository root: {0} -> {1}' -f $file, $resolvedPath)
    }
    $relativePath = $resolvedPath.Substring($rootPrefix.Length).Replace('\', '/')
    # Check the source path before resolution so links are rejected even when they target another in-repo file.
    $currentPath = $root
    foreach ($segment in $file.Split([char[]]@('\', '/'), [StringSplitOptions]::RemoveEmptyEntries)) {
        $currentPath = Join-Path $currentPath $segment
        $item = Get-Item -LiteralPath $currentPath -Force -ErrorAction Stop
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw ('Release source contains a reparse point: ' + $currentPath)
        }
    }
    if (-not $validatedFiles.Contains($relativePath)) { $validatedFiles.Add($relativePath) }
}
$files = @($validatedFiles)
$requiredEntries = @('compose.yml', 'import-aistudio2api-session.cmd', 'import-aistudio2api-session.ps1',
    'tests/Test-Aistudio2ApiImport.ps1')
Add-Type -AssemblyName System.IO.Compression
$archive = Join-Path $dist "ai-proxy-kit-$Version.zip"
$stream = [IO.File]::Open($archive, [IO.FileMode]::Create)
$zip = New-Object IO.Compression.ZipArchive($stream, [IO.Compression.ZipArchiveMode]::Create, $false)
try {
    foreach ($file in $files) {
        # ZIP uses forward slashes even when it is created on Windows.
        $entry = $zip.CreateEntry($file, [IO.Compression.CompressionLevel]::Optimal)
        $source = [IO.File]::OpenRead((Join-Path $root $file))
        $destination = $entry.Open()
        try { $source.CopyTo($destination) }
        finally { $destination.Dispose(); $source.Dispose() }
    }
} finally { $zip.Dispose(); $stream.Dispose() }
$expectedEntries = @($files | ForEach-Object { $_.Replace('\', '/') })
foreach ($requiredEntry in $requiredEntries) {
    if ($expectedEntries -notcontains $requiredEntry) { $expectedEntries += $requiredEntry }
}
$validateStream = [IO.File]::OpenRead($archive)
$validateZip = New-Object IO.Compression.ZipArchive($validateStream, [IO.Compression.ZipArchiveMode]::Read, $false)
try {
    $missingEntries = @()
    foreach ($entryName in $expectedEntries) {
        if ($null -eq $validateZip.GetEntry($entryName)) { $missingEntries += $entryName }
    }
    if ($missingEntries.Count -gt 0) { throw ('Release archive is missing required entries: ' + ($missingEntries -join ', ')) }
} finally { $validateZip.Dispose(); $validateStream.Dispose() }
Write-Output ('Release archive validation passed: {0} files present, including compose.yml and the AIStudio2API session importer.' -f $expectedEntries.Count)
$checksum = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText(($archive + '.sha256'), ($checksum + '  ' + [IO.Path]::GetFileName($archive) + "`n"), [Text.Encoding]::ASCII)
Write-Host "Public script package: $archive"
Write-Host "SHA256: $checksum"
