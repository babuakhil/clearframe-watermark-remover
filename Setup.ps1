$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:UV_CACHE_DIR = Join-Path $PSScriptRoot '.uv-cache'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $PSScriptRoot '.runtime'
function Check-ExitCode { if ($LASTEXITCODE -ne 0) { throw "Setup command failed with exit code $LASTEXITCODE" } }
Write-Host 'Setting up Clearframe in this folder. Videos are never uploaded.'
if (-not (Test-Path -LiteralPath '.bootstrap/bin/uv.exe')) {
    python -m pip install --target .bootstrap uv==0.9.11 --disable-pip-version-check
    Check-ExitCode
}
$uvPath = Join-Path $PSScriptRoot '.bootstrap/bin/uv.exe'
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    & $uvPath python install 3.11.11 --no-registry --no-bin
    Check-ExitCode
    & $uvPath venv --python 3.11.11 .venv
    Check-ExitCode
}
& $uvPath pip install --quiet --python .venv/Scripts/python.exe torch==2.5.1+cu121 torchvision==0.20.1+cu121 --index-url https://download.pytorch.org/whl/cu121
Check-ExitCode
& $uvPath pip install --quiet --python .venv/Scripts/python.exe -r requirements.txt
Check-ExitCode
if (-not (Test-Path -LiteralPath 'vendor/ProPainter/model/propainter.py')) {
    git clone https://github.com/sczhou/ProPainter.git vendor/ProPainter
    Check-ExitCode
    git -C vendor/ProPainter checkout 326782529a63d997cab80ab21439b92ed422b3d3
    Check-ExitCode
}
& .venv/Scripts/python.exe tools/download_models.py
Check-ExitCode
& .venv/Scripts/python.exe -c "import torch; assert torch.cuda.is_available(), 'CUDA GPU not available'; print('GPU:', torch.cuda.get_device_name(0))"
Check-ExitCode
if (-not (Test-Path -LiteralPath 'examples/Watermarked sample.mp4')) {
    & .venv/Scripts/python.exe -c "from tools.benchmark import make_examples; make_examples()"
    Check-ExitCode
}
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
if (Test-Path -LiteralPath $compiler) {
    & .venv/Scripts/python.exe tools/build_launcher.py
    Check-ExitCode
}
Write-Host 'Ready. Double-click Clearframe.exe or Start Clearframe.cmd.'
