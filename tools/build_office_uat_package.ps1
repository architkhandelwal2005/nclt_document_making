param([string]$Destination)

$ErrorActionPreference = "Stop"

function Get-ChildRelativePath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $normalizedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $normalizedPath = [IO.Path]::GetFullPath($Path)
    if (-not $normalizedPath.StartsWith($normalizedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path is outside the expected package source root: $normalizedPath"
    }
    return $normalizedPath.Substring($normalizedRoot.Length)
}

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($Destination)) {
    $Destination = Join-Path $projectRoot "office_uat_package\NCLT_CIRP_UAT"
}
$destinationRoot = [IO.Path]::GetFullPath($Destination)

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "frontend\build\index.html"))) {
    throw "Compiled frontend missing. Run npm run build before creating the office package."
}
if (Test-Path -LiteralPath $destinationRoot) {
    throw "Package destination already exists: $destinationRoot. No files were overwritten."
}
$buildRoot = "$destinationRoot.building"
if (Test-Path -LiteralPath $buildRoot) {
    throw "Incomplete package staging directory exists: $buildRoot. Remove it before retrying."
}

New-Item -ItemType Directory -Path $buildRoot | Out-Null

try {

$rootFiles = @(
    ".env.uat.example", "INSTALL_NCLT_UAT.bat", "SETUP_NCLT_UAT.bat",
    "START_NCLT_UAT.bat", "STOP_NCLT_UAT.bat", "BACKUP_NCLT_UAT.bat",
    "BACKUP_BEFORE_TESTING.bat", "BACKUP_AFTER_TESTING.bat",
    "CREATE_SYNTHETIC_UAT_CASE.bat"
)
foreach ($name in $rootFiles) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $name) -Destination (Join-Path $buildRoot $name)
}

$excludedBackendSegments = @(
    "\tests\", "\venv\", "\data\", "\__pycache__\",
    "\.pytest_cache\", "\.pytest_tmp\", "\templates\custom\"
)
$excludedBackendFiles = @(
    ".env", ".env.example", ".gitignore", "requirements-dev.txt", "pytest.ini",
    "prepare_portable.py", "portable_launcher.py"
)
$backendRoot = Join-Path $projectRoot "backend"
Get-ChildItem -LiteralPath $backendRoot -Recurse -File | Where-Object {
    $path = $_.FullName
    -not ($excludedBackendSegments | Where-Object { $path.IndexOf($_, [StringComparison]::OrdinalIgnoreCase) -ge 0 }) -and
    $excludedBackendFiles -notcontains $_.Name -and
    $_.Name -notlike "phase*_demo.py" -and
    $_.Name -notlike "demo_*.py" -and
    $_.Extension -ne ".pyc"
} | ForEach-Object {
    $relative = Get-ChildRelativePath -Root $backendRoot -Path $_.FullName
    $target = Join-Path (Join-Path $buildRoot "backend") $relative
    New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
    Copy-Item -LiteralPath $_.FullName -Destination $target
}

$frontendRoot = Join-Path $projectRoot "frontend\build"
Get-ChildItem -LiteralPath $frontendRoot -Recurse -File | Where-Object { $_.Extension -ne ".map" } | ForEach-Object {
    $relative = Get-ChildRelativePath -Root $frontendRoot -Path $_.FullName
    $target = Join-Path (Join-Path $buildRoot "frontend\build") $relative
    New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
    Copy-Item -LiteralPath $_.FullName -Destination $target
}

$docsRoot = Join-Path $buildRoot "docs"
New-Item -ItemType Directory -Path $docsRoot | Out-Null
foreach ($name in @("OFFICE_UAT_SETUP.md", "OFFICE_UAT_INSTALL_CHECKLIST.md", "UAT_PHASE6.md")) {
    Copy-Item -LiteralPath (Join-Path $projectRoot "docs\$name") -Destination (Join-Path $docsRoot $name)
}
$toolsRoot = Join-Path $buildRoot "tools"
New-Item -ItemType Directory -Path $toolsRoot | Out-Null
Copy-Item -LiteralPath (Join-Path $projectRoot "tools\stop_nclt_uat.ps1") -Destination (Join-Path $toolsRoot "stop_nclt_uat.ps1")

$contents = @(
    "NCLT CIRP SOFTWARE - OFFICE UAT PACKAGE",
    "Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
    "",
    "Included: compiled frontend, runtime backend, office UAT scripts and UAT guides.",
    "Excluded: secrets, databases, uploads, node_modules, source maps, tests, temporary files,",
    "reference/client documents, developer backups, IDE files and development-only scripts.",
    "",
    "Begin with docs\OFFICE_UAT_SETUP.md and INSTALL_NCLT_UAT.bat."
)
Set-Content -LiteralPath (Join-Path $buildRoot "PACKAGE_CONTENTS.txt") -Value $contents -Encoding UTF8
Move-Item -LiteralPath $buildRoot -Destination $destinationRoot
}
catch {
    if (Test-Path -LiteralPath $buildRoot) {
        Remove-Item -LiteralPath $buildRoot -Recurse -Force
    }
    throw
}

Write-Host "Sanitized office UAT package created:"
Write-Host $destinationRoot
