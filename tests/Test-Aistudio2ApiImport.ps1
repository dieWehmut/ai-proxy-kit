#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$scriptPath = Join-Path $root 'import-aistudio2api-session.ps1'
$commandPath = Join-Path $root 'import-aistudio2api-session.cmd'
$composePath = Join-Path $root 'compose.yml'

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$parseErrors)
Assert-True ($parseErrors.Count -eq 0) 'PowerShell parser rejected import-aistudio2api-session.ps1.'
$text = [IO.File]::ReadAllText($scriptPath)
Assert-True (-not @([IO.File]::ReadAllBytes($scriptPath) | Where-Object { $_ -gt 127 }).Count) 'The AIStudio2API import script must remain ASCII for Windows PowerShell 5.1.'
Assert-True (Test-Path -LiteralPath $commandPath -PathType Leaf) 'The Windows command entry point is missing.'

$parameterNames = @($ast.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
foreach ($name in @('Source', 'Distribution', 'DataDir', 'Container')) {
    Assert-True ($parameterNames -contains $name) "Missing script parameter: $name."
}
$containerParameter = @($ast.ParamBlock.Parameters | Where-Object { $_.Name.VariablePath.UserPath -eq 'Container' })[0]
Assert-True ($containerParameter.StaticType.FullName -eq 'System.String') 'Container must be a string parameter.'
Assert-True ($null -ne $containerParameter.DefaultValue) 'Container must have a default value.'
Assert-True ($containerParameter.DefaultValue.Extent.Text -eq "'ai-proxy-kit-aistudio-1'") 'Container must default to ai-proxy-kit-aistudio-1.'

Assert-True ($text.Contains('HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders')) 'Registry Downloads resolution was removed.'
Assert-True ($text.Contains('{374DE290-123F-4565-9164-39C4925E467B}')) 'Registry Downloads value name was removed.'
Assert-True ($text.Contains("Join-Path `$env:USERPROFILE 'Downloads'")) 'Downloads fallback was removed.'
Assert-True ($text.Contains('ai-proxy-kit-google-session.json')) 'The default exported session filename was removed.'
Assert-True ($text.Contains("Join-Path `$PSScriptRoot 'runtime\aistudio'")) 'The default runtime/aistudio data directory was removed.'
Assert-True ($text.Contains("Join-Path `$DataDir 'session.json'")) 'The copied session must be stored as session.json.'
Assert-True ($text.Contains('Copy-Item -LiteralPath $Source -Destination $sessionDestination -Force')) 'The exported session must be copied into the repository runtime directory.'
Assert-True ($text.Contains("`$containerSessionPath = '/data/session.json'")) 'The documented /data container path was removed.'
Assert-True ($text.Contains('& $wsl @arguments -- docker exec $Container aistudio2api setup --storage-state $containerSessionPath')) 'The AIStudio2API storage-state import command was changed.'
Assert-True ($text.Contains('^\\\\wsl(?:\.localhost|\$)\\')) 'Existing WSL distribution detection was changed.'
Assert-True ($text.Contains("if (`$Distribution) { `$arguments = @('-d', `$Distribution) }")) 'Explicit WSL distribution forwarding was removed.'

$functionDefinitions = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'ConvertTo-WslPath'
}, $true))
Assert-True ($functionDefinitions.Count -eq 1) 'Expected one reusable WSL path conversion function.'
$wslpathCommands = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst] -and
    $node.Extent.Text.Contains('wslpath')
}, $true))
Assert-True ($wslpathCommands.Count -eq 1) 'All Windows paths should use the existing wslpath conversion logic.'
Assert-True ($wslpathCommands[0].Extent.Text.Contains('wslpath -u')) 'WSL paths must be produced with wslpath -u.'

$dataAssignments = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
    $node.Left.Extent.Text -eq '$linuxDataDir'
}, $true))
Assert-True ($dataAssignments.Count -eq 1) 'Expected one WSL conversion of the data directory.'
Assert-True ($dataAssignments[0].Right.Extent.Text.Contains('ConvertTo-WslPath')) 'The data directory must use ConvertTo-WslPath.'
Assert-True ($text.Contains('chmod 600 $linuxSessionPath')) 'The copied session must be restricted to owner read/write access.'

foreach ($commandName in @('Write-Host', 'Write-Output', 'Write-Verbose', 'Write-Information', 'Write-Debug', 'Out-Host', 'Out-String', 'Format-List', 'ConvertTo-Json', 'Get-Content', 'Select-String')) {
    $commands = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.CommandAst] -and
        $node.GetCommandName() -eq $commandName
    }, $true))
    Assert-True ($commands.Count -eq 0) "The import script must not print session data with $commandName."
}
foreach ($commandName in @('Invoke-WebRequest', 'Invoke-RestMethod', 'curl', 'wget')) {
    $commands = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.CommandAst] -and
        $node.GetCommandName() -eq $commandName
    }, $true))
    Assert-True ($commands.Count -eq 0) "Static AIStudio2API import checks must not perform network requests with $commandName."
}

$commandText = [IO.File]::ReadAllText($commandPath)
Assert-True ($commandText.Contains('"%~dp0import-aistudio2api-session.ps1" %*')) 'The cmd entry point must forward all arguments to the PowerShell script.'
Assert-True ($commandText.Contains('exit /b %importExitCode%')) 'The cmd entry point must preserve the PowerShell exit code.'
foreach ($commandName in @('type ', 'more ', 'findstr ')) {
    Assert-True (-not $commandText.ToLowerInvariant().Contains($commandName)) "The cmd entry point must not print session data with $commandName."
}

Assert-True (Test-Path -LiteralPath $composePath -PathType Leaf) 'compose.yml is missing.'
$composeText = [IO.File]::ReadAllText($composePath)
Assert-True ($composeText -match '(?m)^name:\s*ai-proxy-kit\s*$') 'compose.yml must keep the ai-proxy-kit project name that produces the default container name.'
$aistudioMatch = [regex]::Match($composeText, '(?ms)^  aistudio:\s*\r?\n(.*?)(?=^\S|\z)')
Assert-True $aistudioMatch.Success 'compose.yml is missing the aistudio service.'
$aistudioBlock = $aistudioMatch.Groups[1].Value
$imageMatch = [regex]::Match($aistudioBlock, '(?m)^\s+image:\s*(.+)$')
Assert-True $imageMatch.Success 'The aistudio service has no image.'
Assert-True ($imageMatch.Groups[1].Value.Trim() -match 'aistudio2api') 'The aistudio service image must be aistudio2api.'
$portsMatch = [regex]::Match($aistudioBlock, '(?m)^\s+ports:\s*\[[^\]]*\]')
Assert-True $portsMatch.Success 'The aistudio service has no ports mapping.'
Assert-True ($portsMatch.Value -match ':\$\{AISTUDIO_PORT:-2048\}:2048|:2048\s*\]') 'The aistudio service must expose container port 2048.'
Assert-True ($aistudioBlock -match '(?m)^\s+-\s+\./runtime/aistudio/data:/data\s*$') 'The aistudio service must mount ./runtime/aistudio/data at /data.'

Write-Host 'PASS: AIStudio2API import parameters, path handling, compose service, conditional forwarding, and non-disclosure checks.'
