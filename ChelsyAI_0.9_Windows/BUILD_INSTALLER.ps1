$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
Write-Host 'Chelsy AI - Windows installer build' -ForegroundColor Cyan

try {
    & py -3.11 --version
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 is required. Install Python 3.11 with Python Launcher.' }

    if (-not (Test-Path '.buildvenv\Scripts\python.exe')) {
        & py -3.11 -m venv .buildvenv
        if ($LASTEXITCODE -ne 0) { throw 'Failed to create virtual environment.' }
    }

    $python = Join-Path $PSScriptRoot '.buildvenv\Scripts\python.exe'

    & $python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'Failed to update pip.' }

    & $python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Failed to install dependencies.' }

    & $python -m unittest discover -v
    if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed. Build stopped.' }

    & $python preflight.py
    if ($LASTEXITCODE -ne 0) { throw 'Gradio/UI preflight failed. Build stopped.' }

    & $python -m PyInstaller --noconfirm --clean --onedir --windowed --name ChelsyAI --icon assets\chelsy.ico --add-data "assets;assets" --collect-all gradio --collect-all gradio_client --collect-all safehttpx --collect-all huggingface_hub --collect-all groovy --collect-all orjson --collect-all gradio launcher.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

    # Private host process: console is useful for diagnostics. Ollama never exposed directly.
    & $python -m PyInstaller --noconfirm --clean --onedir --console --name ChelsyHost host_server.py
    if ($LASTEXITCODE -ne 0) { throw 'Private host build failed.' }

    # Lightweight Windows client without local Ollama dependency.
    & $python -m PyInstaller --noconfirm --clean --onedir --windowed --name ChelsyClient --icon assets\chelsy.ico client.py
    if ($LASTEXITCODE -ne 0) { throw 'Client build failed.' }

    & $python -m PyInstaller --noconfirm --clean --onedir --windowed --name ChelsyConfig --icon assets\chelsy.ico --add-data "site_supabase_public.json;." configure_supabase.py
    if ($LASTEXITCODE -ne 0) { throw 'Supabase config tool build failed.' }

    & $python -m PyInstaller --noconfirm --clean --onedir --console --name ChelsyMigrate migrate_local.py
    if ($LASTEXITCODE -ne 0) { throw 'Migration tool build failed.' }

    & $python -m PyInstaller --noconfirm --clean --onedir --console --name ChelsyUpload upload_existing_to_supabase.py
    if ($LASTEXITCODE -ne 0) { throw 'Cloud upload tool build failed.' }

    $candidates = @()
    if ($env:CHELSY_ISCC_PATH) { $candidates += $env:CHELSY_ISCC_PATH }
    $candidates += @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 7\ISCC.exe",
        "${env:LOCALAPPDATA}\Programs\Inno Setup 7\ISCC.exe"
    )
    $iscc = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
    if (-not $iscc) {
        $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($command) { $iscc = $command.Source }
    }
    if (-not $iscc) {
        # Inno Setup may be installed in a custom path. Read its registered uninstall location.
        $roots = @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
                   'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
                   'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall')
        foreach ($root in $roots) {
            if (-not (Test-Path $root)) { continue }
            foreach ($item in (Get-ChildItem $root -ErrorAction SilentlyContinue)) {
                $info = Get-ItemProperty $item.PSPath -ErrorAction SilentlyContinue
                if ($info.DisplayName -match '^Inno Setup (6|7)') {
                    $path = Join-Path $info.InstallLocation 'ISCC.exe'
                    if ($info.InstallLocation -and (Test-Path -LiteralPath $path)) {
                        $iscc = $path
                        break
                    }
                }
            }
            if ($iscc) { break }
        }
    }
    if (-not $iscc) { throw 'Inno Setup compiler not found. Open ChelsyAI.iss in Inno Setup and press F9, or set CHELSY_ISCC_PATH.' }


    if (-not (Test-Path 'dist\ChelsyUpload\ChelsyUpload.exe')) { throw 'Cloud upload EXE missing.' }
    if (-not (Test-Path 'dist\ChelsyMigrate\ChelsyMigrate.exe')) { throw 'Migration EXE missing.' }
    if (-not (Test-Path 'dist\ChelsyConfig\ChelsyConfig.exe')) { throw 'Supabase configuration EXE missing.' }
    if (-not (Test-Path 'dist\ChelsyClient\ChelsyClient.exe')) { throw 'Client EXE missing.' }
    if (-not (Test-Path 'dist\ChelsyHost\ChelsyHost.exe')) { throw 'Host EXE missing.' }
    if (-not (Test-Path 'dist\ChelsyAI\ChelsyAI.exe')) { throw 'Packaged application EXE not found.' }
    if (-not (Test-Path 'dist\ChelsyAI\_internal\assets\chelsy_logo_small.png')) { throw 'Brand asset not bundled correctly.' }
    if (-not (Test-Path 'dist\ChelsyAI\_internal\groovy\version.txt')) { throw 'Missing groovy/version.txt in package.' }

    & $iscc (Join-Path $PSScriptRoot 'ChelsyAI.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed.' }

    & $iscc (Join-Path $PSScriptRoot 'CLIENT_ONLY.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Separate Client installer build failed.' }
    Write-Host "SUCCESS: $PSScriptRoot\installer\ChelsyAI_Setup_0.9.exe" -ForegroundColor Green
    Write-Host "SUCCESS: $PSScriptRoot\installer\ChelsyAI_Client_Setup_0.9.exe" -ForegroundColor Green
}
catch {
    Write-Host ('BUILD FAILED: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
