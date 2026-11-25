#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$scriptPath = Join-Path $root 'import-aistudio-session.ps1'

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Get-ConditionalBranch {
    param([System.Management.Automation.Language.Ast]$Statement)
    $branch = $Statement.Parent
    while ($null -ne $branch -and $branch -isnot [System.Management.Automation.Language.IfStatementAst]) {
        $branch = $branch.Parent
    }
    return $branch
}

$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref]$tokens, [ref]$parseErrors)
Assert-True ($parseErrors.Count -eq 0) 'PowerShell parser rejected import-aistudio-session.ps1.'
$text = [IO.File]::ReadAllText($scriptPath)
Assert-True (-not @([IO.File]::ReadAllBytes($scriptPath) | Where-Object { $_ -gt 127 }).Count) 'The session import script must remain ASCII for Windows PowerShell 5.1.'

$parameterNames = @($ast.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
foreach ($name in @('Source', 'Distribution', 'AuthDir', 'BaseUrl', 'EnvFile')) {
    Assert-True ($parameterNames -contains $name) "Missing script parameter: $name."
}
foreach ($name in @('AuthDir', 'BaseUrl', 'EnvFile')) {
    $parameter = @($ast.ParamBlock.Parameters | Where-Object { $_.Name.VariablePath.UserPath -eq $name })[0]
    Assert-True ($parameter.StaticType.FullName -eq 'System.String') "Optional parameter $name must be a string."
    $mandatory = @($parameter.Attributes | Where-Object {
        $_.TypeName.Name -eq 'Parameter' -and
        @($_.NamedArguments | Where-Object {
            $_.ArgumentName.Value -eq 'Mandatory' -and $_.Argument.Extent.Text -match '^\$true$'
        }).Count -gt 0
    })
    Assert-True ($mandatory.Count -eq 0) "Optional parameter $name must not be mandatory."
}

Assert-True ($text.Contains('HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders')) 'Registry Downloads resolution was removed.'
Assert-True ($text.Contains('{374DE290-123F-4565-9164-39C4925E467B}')) 'Registry Downloads value name was removed.'
Assert-True ($text.Contains("Join-Path `$env:USERPROFILE 'Downloads'")) 'Downloads fallback was removed.'
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

$assignments = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
    $node.Left.Extent.Text -eq '$pythonArguments'
}, $true))
foreach ($flag in @('--auth-dir', '--base-url', '--env-file')) {
    $matches = @($assignments | Where-Object { $_.Right.Extent.Text.Contains($flag) })
    Assert-True ($matches.Count -eq 1) "Expected one conditional Python argument assignment for $flag."
    $branch = Get-ConditionalBranch $matches[0]
    Assert-True ($null -ne $branch) "Python argument $flag must be conditional."
    $condition = $branch.Clauses[0].Item1.Extent.Text
    $parameterName = @{
        '--auth-dir' = 'AuthDir'
        '--base-url' = 'BaseUrl'
        '--env-file' = 'EnvFile'
    }[$flag]
    Assert-True ($condition.Contains("ContainsKey('$parameterName')")) "$flag must be gated by PSBoundParameters.$parameterName."
    $body = $branch.Clauses[0].Item2.Extent.Text
    Assert-True ($body.Contains($flag)) "$flag was not added inside its parameter branch."
}

$authAssignment = @($assignments | Where-Object { $_.Right.Extent.Text.Contains('--auth-dir') })[0]
$envAssignment = @($assignments | Where-Object { $_.Right.Extent.Text.Contains('--env-file') })[0]
$baseAssignment = @($assignments | Where-Object { $_.Right.Extent.Text.Contains('--base-url') })[0]
Assert-True ($authAssignment.Right.Extent.Text.Contains('$linuxAuthDir')) 'AuthDir must be converted to a WSL path before forwarding.'
Assert-True ((Get-ConditionalBranch $authAssignment).Clauses[0].Item2.Extent.Text.Contains('ConvertTo-WslPath')) 'AuthDir must use ConvertTo-WslPath.'
Assert-True ($envAssignment.Right.Extent.Text.Contains('$linuxEnvFile')) 'EnvFile must be converted to a WSL path before forwarding.'
Assert-True ((Get-ConditionalBranch $envAssignment).Clauses[0].Item2.Extent.Text.Contains('ConvertTo-WslPath')) 'EnvFile must use ConvertTo-WslPath.'
Assert-True ($baseAssignment.Right.Extent.Text.Contains('$BaseUrl')) 'BaseUrl must be forwarded directly.'
Assert-True (-not (Get-ConditionalBranch $baseAssignment).Clauses[0].Item2.Extent.Text.Contains('ConvertTo-WslPath')) 'BaseUrl is a URL and must not be converted as a filesystem path.'

foreach ($flag in @('--auth-dir', '--base-url', '--env-file')) {
    Assert-True ([regex]::Matches($text, [regex]::Escape("'$flag'")).Count -eq 1) "$flag must only be emitted through its optional branch."
}
Assert-True ($text.Contains('& $wsl @arguments --exec python3 @pythonArguments')) 'The Python helper invocation must receive the assembled argument array.'

foreach ($commandName in @('Write-Host', 'Write-Output', 'Write-Verbose', 'Write-Information', 'Out-Host')) {
    $commands = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.CommandAst] -and
        $node.GetCommandName() -eq $commandName
    }, $true))
    Assert-True ($commands.Count -eq 0) "The import script must not print parameters or secrets with $commandName."
}
foreach ($commandName in @('Invoke-WebRequest', 'Invoke-RestMethod', 'curl', 'wget')) {
    $commands = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.CommandAst] -and
        $node.GetCommandName() -eq $commandName
    }, $true))
    Assert-True ($commands.Count -eq 0) "Static session import checks must not perform network requests with $commandName."
}

Write-Host 'PASS: Session import parameters, path conversion, conditional forwarding, and non-disclosure checks.'
