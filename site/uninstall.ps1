# Remove the Shorts Everywhere helper (Windows). In PowerShell:
#   irm https://minefoundation.org/uninstall.ps1 | iex
# Keeps %USERPROFILE%\.shorts-everywhere\chrome_profile (your platform sign-ins) and uploads. To remove those too:
#   $env:SHORTS_UNINSTALL_ALL = '1'; irm https://minefoundation.org/uninstall.ps1 | iex

function Uninstall-ShortsEverywhereHelper {
    $ErrorActionPreference = 'Stop'
    $App = Join-Path $env:USERPROFILE '.shorts-everywhere'
    Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
        Where-Object { $_.CommandLine -like '*helper.local_app*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1
    $Shortcut = Join-Path ([Environment]::GetFolderPath('Startup')) 'Shorts Everywhere helper.lnk'
    if (Test-Path $Shortcut) { Remove-Item -Force $Shortcut }
    if ($env:SHORTS_UNINSTALL_ALL -eq '1') {
        if (Test-Path $App) { Remove-Item -Recurse -Force $App }
        Write-Host 'Removed the helper and all its data, including saved platform sign-ins.'
    } else {
        foreach ($Part in 'app', 'app.new', 'venv', 'bin', 'helper.log') {
            $Path = Join-Path $App $Part
            if (Test-Path $Path) { Remove-Item -Recurse -Force $Path }
        }
        Write-Host "Removed the helper. Your sign-ins and uploads are still in $App (delete that folder to remove them too)."
    }
}

Uninstall-ShortsEverywhereHelper
