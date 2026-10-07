# Shorts Everywhere helper installer (Windows). In PowerShell:
#   irm https://minefoundation.org/install.ps1 | iex
#
# Installs everything under %USERPROFILE%\.shorts-everywhere (no admin rights, nothing system-wide):
#   app\      the helper code, from github.com/deswitsservice/shorts-scheduler
#   venv\     its own Python (fetched by uv), so whatever Python is on the PC doesn't matter
#   chrome_profile\, uploads\, shots\   your data; platform sign-ins stay on this computer
# plus a shortcut in your Startup folder so the helper starts at sign-in (hidden, no console window).
# Remove it with:  irm https://minefoundation.org/uninstall.ps1 | iex
#
# For testing: $env:SHORTS_REF = '<branch>' installs another branch; $env:SHORTS_SOURCE_DIR = '<path>' copies a local
# checkout instead; $env:SHORTS_NO_OPEN = '1' skips opening the website at the end.
# Everything runs inside one function so a half-downloaded script (irm | iex) can't run partially.

function Install-ShortsEverywhereHelper {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest's progress bar makes downloads very slow
    $App = Join-Path $env:USERPROFILE '.shorts-everywhere'
    $Repo = 'deswitsservice/shorts-scheduler'
    $Ref = if ($env:SHORTS_REF) { $env:SHORTS_REF } else { 'master' }
    $Port = 8765
    function Say($Message) { Write-Host "==> $Message" -ForegroundColor Magenta }

    $Chrome = @(
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if (-not $Chrome) { throw 'Install Google Chrome first (https://www.google.com/chrome/), then run this again.' }
    New-Item -ItemType Directory -Force -Path $App | Out-Null

    $Bin = Join-Path $App 'bin'
    $Uv = Join-Path $Bin 'uv.exe'
    if (-not (Test-Path $Uv)) {
        Say 'Downloading uv (Python manager)...'
        $env:UV_INSTALL_DIR = $Bin; $env:UV_NO_MODIFY_PATH = '1'; $env:INSTALLER_NO_MODIFY_PATH = '1'
        powershell -NoProfile -ExecutionPolicy Bypass -Command 'irm https://astral.sh/uv/install.ps1 | iex' | Out-Null
        if (-not (Test-Path $Uv)) { throw "Couldn't install uv. Check your internet connection and try again." }
    }

    Say 'Getting the helper...'
    $New = Join-Path $App 'app.new'
    if (Test-Path $New) { Remove-Item -Recurse -Force $New }
    if ($env:SHORTS_SOURCE_DIR) {
        New-Item -ItemType Directory -Force -Path $New | Out-Null
        Copy-Item -Path (Join-Path $env:SHORTS_SOURCE_DIR '*') -Destination $New -Recurse -Force
    } else {
        $Zip = Join-Path $env:TEMP 'shorts-everywhere-helper.zip'
        $Unzip = Join-Path $env:TEMP 'shorts-everywhere-helper'
        Invoke-WebRequest -UseBasicParsing "https://codeload.github.com/$Repo/zip/refs/heads/$Ref" -OutFile $Zip
        if (Test-Path $Unzip) { Remove-Item -Recurse -Force $Unzip }
        Expand-Archive -Path $Zip -DestinationPath $Unzip -Force
        Move-Item -Path (Get-ChildItem $Unzip | Select-Object -First 1).FullName -Destination $New
        Remove-Item -Force $Zip
    }
    if (-not (Test-Path (Join-Path $New 'helper\local_app.py'))) { throw "The download didn't contain the helper. Try again later." }

    Say 'Setting up Python and packages (first time takes a minute)...'
    $Venv = Join-Path $App 'venv'
    & $Uv venv --quiet --python 3.12 --allow-existing $Venv
    if ($LASTEXITCODE) { throw 'Setting up Python failed.' }
    & $Uv pip install --quiet --python (Join-Path $Venv 'Scripts\python.exe') -r (Join-Path $New 'helper\requirements.txt')
    if ($LASTEXITCODE) { throw 'Installing the helper packages failed.' }

    # Stop the old helper, then swap in the new code only now that everything installed (a failed update leaves the
    # old one in place).
    Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
        Where-Object { $_.CommandLine -like '*helper.local_app*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1
    $AppDir = Join-Path $App 'app'
    if (Test-Path $AppDir) { Remove-Item -Recurse -Force $AppDir }
    Move-Item -Path $New -Destination $AppDir

    Say 'Starting the helper (and at every sign-in)...'
    $Pythonw = Join-Path $Venv 'Scripts\pythonw.exe'
    $Shortcut = Join-Path ([Environment]::GetFolderPath('Startup')) 'Shorts Everywhere helper.lnk'
    $Link = (New-Object -ComObject WScript.Shell).CreateShortcut($Shortcut)
    $Link.TargetPath = $Pythonw
    $Link.Arguments = '-u -m helper.local_app'
    $Link.WorkingDirectory = $AppDir
    $Link.WindowStyle = 7   # minimized; pythonw has no console window anyway
    $Link.Save()
    Start-Process -FilePath $Pythonw -ArgumentList '-u', '-m', 'helper.local_app' -WorkingDirectory $AppDir -WindowStyle Hidden

    for ($i = 0; $i -lt 90; $i++) {
        try {
            Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/api/helper/info" -TimeoutSec 2 | Out-Null
            Say 'Done. The helper is running. Opening Shorts Everywhere...'
            if (-not $env:SHORTS_NO_OPEN) { Start-Process 'https://minefoundation.org/' }
            return
        } catch { Start-Sleep -Seconds 1 }
    }
    throw "The helper didn't start. See $App\helper.log"
}

Install-ShortsEverywhereHelper
