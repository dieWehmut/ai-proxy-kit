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
    'tools\Build-Release.ps1', 'install.sh', 'start.sh', 'stop.sh', 'status.sh', 'check.sh',
    'connect.sh', 'access.sh', 'install.cmd', 'start.cmd', 'stop.cmd', 'status.cmd', 'check.cmd',
    'open-aistudio.cmd', 'import-aistudio-session.cmd', 'import-aistudio-session.ps1',
    'proxykit.ps1', 'compose.yml', 'compose.workbuddy-proxy.yml', 'compose.aistudio-novnc.yml', '.env.example',
    'lib\proxykit.py', 'scripts\connect-sub2api.py', 'tools\build-workbuddy-proxy.py',
    'tests\test_proxykit.py', 'docs\SUB2API_WINDOWS.md', 'docs\AISTUDIO_COMPARISON.md',
    'docs\VALIDATION.md', 'docs\AISTUDIO_LOCAL_LOGIN.md', 'docs\WINDOWS_AISTUDIO_INSTALL.md',
    'docs\validation\windows-aistudio-proxy-20261124.md', 'scripts\import-aistudio-session.py',
    'tools\google-session-extension\manifest.json', 'tools\google-session-extension\background.js',
    'tools\google-session-extension\export.html', 'tools\google-session-extension\export.mjs',
    'tests\test_aistudio_session.py', 'tests\test_google_session_export.mjs')
$novncRoot = Join-Path $root 'services\aistudio-novnc'
$novncVendorRoot = Join-Path $novncRoot 'vendor\novnc'
if (-not (Test-Path -LiteralPath $novncRoot -PathType Container)) { throw 'Required release source directory is missing: services\aistudio-novnc' }
if (-not (Test-Path -LiteralPath $novncVendorRoot -PathType Container)) { throw 'Required release source directory is missing: services\aistudio-novnc\vendor\novnc' }
foreach ($novncDirectory in @($novncRoot, (Join-Path $novncRoot 'vendor'), $novncVendorRoot)) {
    $directoryItem = Get-Item -LiteralPath $novncDirectory -Force -ErrorAction Stop
    if (($directoryItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw ('Release source contains a reparse point: ' + $novncDirectory)
    }
}

# Keep the release payload explicit: two entry files plus the vendored noVNC tree.
$novncAllowlist = @('services\aistudio-novnc\preload.cjs', 'services\aistudio-novnc\novnc.cjs')
$novncVendorFiles = @(Get-ChildItem -LiteralPath $novncVendorRoot -File -Recurse | Sort-Object FullName | ForEach-Object {
    $_.FullName.Substring($root.Length).TrimStart([char[]]@('\', '/')).Replace('\', '/')
})
$novncFiles = @($novncAllowlist) + $novncVendorFiles
if ($novncVendorFiles.Count -eq 0) { throw 'No files found under services\aistudio-novnc\vendor\novnc.' }
$files += $novncFiles
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
$requiredEntries = @('compose.aistudio-novnc.yml', 'import-aistudio-session.cmd', 'import-aistudio-session.ps1',
    'services/aistudio-novnc/novnc.cjs', 'services/aistudio-novnc/preload.cjs')
$requiredNovncVendorCount = 103
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
    $novncEntries = @($validateZip.Entries | Where-Object { $_.FullName.StartsWith('services/aistudio-novnc/', [StringComparison]::Ordinal) })
    $novncVendorEntries = @($novncEntries | Where-Object { $_.FullName.StartsWith('services/aistudio-novnc/vendor/', [StringComparison]::Ordinal) })
    if ($novncEntries.Count -eq 0) { throw 'Release archive is missing services/aistudio-novnc/.' }
    if ($novncVendorEntries.Count -lt $requiredNovncVendorCount) {
        throw ('Release archive services/aistudio-novnc/vendor tree is incomplete: expected at least {0} files, found {1}.' -f $requiredNovncVendorCount, $novncVendorEntries.Count)
    }
} finally { $validateZip.Dispose(); $validateStream.Dispose() }
Write-Output ('Release archive validation passed: {0} required files present, including compose.aistudio-novnc.yml, import-aistudio-session.cmd/.ps1, and services/aistudio-novnc ({1} files; {2} vendor files, minimum {3}).' -f $expectedEntries.Count, $novncEntries.Count, $novncVendorEntries.Count, $requiredNovncVendorCount)
$checksum = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText(($archive + '.sha256'), ($checksum + '  ' + [IO.Path]::GetFileName($archive) + "`n"), [Text.Encoding]::ASCII)
Write-Host "Public script package: $archive"
Write-Host "SHA256: $checksum"
