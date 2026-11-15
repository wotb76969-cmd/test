$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
try {
    & py -3.11 --version
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 not found' }
    if (-not (Test-Path '.buildvenv\Scripts\python.exe')) { & py -3.11 -m venv .buildvenv }
    $py=Join-Path $PSScriptRoot '.buildvenv\Scripts\python.exe'
    & $py -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'pip failed' }
    & $py -m unittest discover -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
    & $py -m PyInstaller --noconfirm --clean --onedir --windowed --name ChelsyClient --icon assets\chelsy.ico client.py
    if ($LASTEXITCODE -ne 0) { throw 'Client build failed' }
    & $py -m PyInstaller --noconfirm --clean --onedir --windowed --name ChelsyConfig --icon assets\chelsy.ico --add-data 'site_supabase_public.json;.' configure_supabase.py
    if ($LASTEXITCODE -ne 0) { throw 'Config build failed' }
    $candidates=@()
    if ($env:CHELSY_ISCC_PATH) { $candidates += $env:CHELSY_ISCC_PATH }
    $candidates += @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe","$env:ProgramFiles\Inno Setup 6\ISCC.exe","$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe","${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe")
    $compiler=$candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if (-not $compiler) { $command=Get-Command ISCC.exe -ErrorAction SilentlyContinue; if ($command) { $compiler=$command.Source } }
    if (-not $compiler) { throw 'Inno Setup 6/7 missing. Install it or set CHELSY_ISCC_PATH' }
    & $compiler (Join-Path $PSScriptRoot 'CLIENT_ONLY.iss')
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed' }
    Write-Host 'READY: installer\ChelsyAI_Client_Setup_0.9.exe' -ForegroundColor Green
} catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
