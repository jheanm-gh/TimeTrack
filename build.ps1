<#
.SYNOPSIS
    Builds TimeTrack into a one-folder Windows application.

.DESCRIPTION
    A clean build, end to end: it creates the virtual environment, installs
    the pinned dependencies, runs the test suite, generates the icon and the
    Windows version resource, packages the application with PyInstaller, and
    creates the desktop and Start Menu shortcuts.

    Run it from the project folder:

        .\build.ps1

    The result is dist\TimeTrack\ containing TimeTrack.exe. That folder is
    the application - keep it together, and use the shortcuts to start it.

.PARAMETER SkipTests
    Skip the test suite. Faster, but you are then packaging something you
    have not checked.

.PARAMETER NoShortcuts
    Do not create the desktop and Start Menu shortcuts.

.PARAMETER KeepSoftwareOpenGL
    Include Mesa's software OpenGL fallback (about 20 MB). Not needed by a
    Qt Widgets application, so it is left out by default. Use this if the
    window ever fails to appear on a particular machine.

.PARAMETER Python
    The Python launcher command to build with. Defaults to "py -3.12".
#>

[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$NoShortcuts,
    [switch]$KeepSoftwareOpenGL,
    [string]$Python = "py"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$AppName      = "TimeTrack"
$ProjectRoot  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$VenvDir      = Join-Path $ProjectRoot ".venv"
$VenvPython   = Join-Path $VenvDir "Scripts\python.exe"
$BuildDir     = Join-Path $ProjectRoot "build"
$DistDir      = Join-Path $ProjectRoot "dist"
$AppDir       = Join-Path $DistDir $AppName
$ExePath      = Join-Path $AppDir "$AppName.exe"

function Write-Step($message) {
    Write-Host ""
    Write-Host "==> $message" -ForegroundColor Cyan
}

function Write-Ok($message) {
    Write-Host "    $message" -ForegroundColor Green
}

function Invoke-Checked($description, $command, $arguments) {
    & $command @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$description failed (exit code $LASTEXITCODE)."
    }
}

Write-Host ""
Write-Host "Building $AppName" -ForegroundColor White
Write-Host "Project folder: $ProjectRoot"

# -- 1. the interpreter ----------------------------------------------------

Write-Step "Checking Python"
if (-not (Test-Path $VenvPython)) {
    Write-Host "    No virtual environment yet - creating one."
    if ($Python -eq "py") {
        Invoke-Checked "Creating the virtual environment" "py" @("-3.12", "-m", "venv", $VenvDir)
    } else {
        Invoke-Checked "Creating the virtual environment" $Python @("-m", "venv", $VenvDir)
    }
}
$versionText = & $VenvPython "--version"
Write-Ok $versionText
if ($versionText -notmatch "3\.12\.") {
    Write-Warning "Expected Python 3.12. Every dependency has a prebuilt wheel for 3.12; other versions may need a compiler."
}

# -- 2. dependencies -------------------------------------------------------

Write-Step "Installing pinned dependencies"
Invoke-Checked "Upgrading pip" $VenvPython @("-m", "pip", "install", "--quiet", "--upgrade", "pip")
Invoke-Checked "Installing requirements" $VenvPython @("-m", "pip", "install", "--quiet", "-r", (Join-Path $ProjectRoot "requirements-dev.txt"))
Write-Ok "Dependencies installed from requirements-dev.txt"

# -- 3. tests --------------------------------------------------------------

if ($SkipTests) {
    Write-Step "Skipping the tests (-SkipTests was given)"
} else {
    Write-Step "Running the test suite"
    Invoke-Checked "The test suite" $VenvPython @("-m", "pytest", "-q")
    Write-Ok "All tests passed"
}

# -- 4. clean --------------------------------------------------------------

Write-Step "Clearing the previous build"
foreach ($path in @($DistDir, (Join-Path $BuildDir "pyi"), $AppDir)) {
    if (Test-Path $path) {
        Remove-Item -Recurse -Force $path
    }
}
Write-Ok "Previous build removed"

# -- 5. icon and version resource -----------------------------------------

Write-Step "Generating the icon and version resource"
Invoke-Checked "Building the icon" $VenvPython @((Join-Path $ProjectRoot "tools\make_icon.py"), "--out", (Join-Path $BuildDir "$AppName.ico"))
Invoke-Checked "Building the version resource" $VenvPython @((Join-Path $ProjectRoot "tools\make_version_file.py"), "--out", (Join-Path $BuildDir "version_info.txt"))

# -- 6. package ------------------------------------------------------------

Write-Step "Packaging with PyInstaller (one folder)"
if ($KeepSoftwareOpenGL) {
    $env:TIMETRACK_KEEP_SOFTWARE_OPENGL = "1"
    Write-Host "    Including the software OpenGL fallback (about 20 MB extra)."
} else {
    Remove-Item Env:\TIMETRACK_KEEP_SOFTWARE_OPENGL -ErrorAction SilentlyContinue
}

Invoke-Checked "PyInstaller" $VenvPython @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--distpath", $DistDir,
    "--workpath", (Join-Path $BuildDir "pyi"),
    (Join-Path $ProjectRoot "$AppName.spec")
)

if (-not (Test-Path $ExePath)) {
    throw "The build finished but $ExePath is missing."
}

$bytes = (Get-ChildItem -Recurse -File $AppDir | Measure-Object -Property Length -Sum).Sum
$megabytes = [math]::Round($bytes / 1MB, 1)
$fileCount = (Get-ChildItem -Recurse -File $AppDir | Measure-Object).Count
Write-Ok "Built $ExePath"
Write-Ok "Folder size: $megabytes MB across $fileCount files"
if ($megabytes -gt 100) {
    Write-Warning "The folder is larger than 100 MB. Check the exclusion lists in $AppName.spec."
}

# -- 7. shortcuts ----------------------------------------------------------

function New-Shortcut($targetPath, $shortcutPath, $description, $iconPath) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $targetPath
    $shortcut.WorkingDirectory = (Split-Path -Parent $targetPath)
    $shortcut.Description = $description
    if (Test-Path $iconPath) {
        $shortcut.IconLocation = $iconPath
    }
    $shortcut.Save()
}

if ($NoShortcuts) {
    Write-Step "Skipping shortcuts (-NoShortcuts was given)"
} else {
    Write-Step "Creating shortcuts"
    $iconPath = Join-Path $BuildDir "$AppName.ico"
    $description = "$AppName - timesheet companion"

    $desktop = [Environment]::GetFolderPath("Desktop")
    $desktopLink = Join-Path $desktop "$AppName.lnk"
    New-Shortcut $ExePath $desktopLink $description $iconPath
    Write-Ok "Desktop: $desktopLink"

    $startMenu = Join-Path ([Environment]::GetFolderPath("ApplicationData")) "Microsoft\Windows\Start Menu\Programs"
    $startLink = Join-Path $startMenu "$AppName.lnk"
    New-Shortcut $ExePath $startLink $description $iconPath
    Write-Ok "Start Menu: $startLink"
}

# -- 8. what to do next ----------------------------------------------------

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host ""
Write-Host "  The application is the whole folder:"
Write-Host "      $AppDir"
Write-Host "  Start it from the desktop shortcut, or by running:"
Write-Host "      $ExePath"
Write-Host ""
Write-Host "  If antivirus quarantines it, give your IT team this folder path"
Write-Host "  to allow. The application makes no network calls of any kind."
Write-Host ""
