param(
    [ValidateSet('HkxReader', 'Resources', 'Pose', 'SceneBinding', 'Configuration', 'Library', 'Installation', 'Authoring', 'HandPose', 'PartGrip', 'GripViewport', 'Mechanism', 'ReloadRecipe', 'Simulation', 'Ammunition', 'ActionCatalog', 'Sound', 'GrabLocations', 'ScopeProfile', 'Collision', 'WeaponModel', 'Checklist', 'Detector')]
    [string]$Suite = 'HkxReader',
    # Machine locations arrive from CTest, which reads them from CMakeUserPresets.json.
    [Parameter(Mandatory = $true)][string]$QtBin,
    [Parameter(Mandatory = $true)][string]$NifSkopeSource,
    [Parameter(Mandatory = $true)][string]$RockSource,
    # Fixture paths passed after the named parameters reach the suite unchanged.
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$TestArguments = @()
)

$ErrorActionPreference = 'Stop'
$taskRepo = Split-Path $PSScriptRoot -Parent
$taskBuild = Join-Path $taskRepo "build-tests/$Suite"
New-Item -ItemType Directory -Path $taskBuild -Force | Out-Null
$env:TEMP=Join-Path $taskRepo 'local/tmp'
$env:TMP=$env:TEMP
$taskOldPath = $env:PATH
try {
    # MSYS2 keeps its shell tools in <msys root>\usr\bin, two levels above the UCRT64 bin folder.
    $msysUsrBin = [System.IO.Path]::GetFullPath((Join-Path $QtBin '..\..\usr\bin'))
    $env:PATH = "$QtBin;$msysUsrBin;$env:PATH"
    Push-Location $taskBuild
    try {
        & (Join-Path $QtBin 'qmake6.exe') (Join-Path $taskRepo "tests/${Suite}Tests.pro") 'CONFIG+=release' "NIFSKOPE_SOURCE_DIR=$($NifSkopeSource -replace '\\','/')" "ROCK_SOURCE_DIR=$($RockSource -replace '\\','/')"
        if ($LASTEXITCODE -ne 0) { throw "$Suite test configuration failed" }
        & (Join-Path $QtBin 'mingw32-make.exe') '-j2'
        if ($LASTEXITCODE -ne 0) { throw "$Suite test build failed" }
        & (Join-Path $taskBuild "${Suite}Tests.exe") @TestArguments
        if ($LASTEXITCODE -ne 0) { throw "$Suite tests failed" }
    } finally {
        Pop-Location
    }
} finally {
    $env:PATH = $taskOldPath
}
