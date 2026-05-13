# スクリプトが存在するディレクトリへ移動
Set-Location -Path $PSScriptRoot

if (-Not (Test-Path "backend\venv")) {
    Write-Host "[setup] venv を作成します..."
    
    # py コマンドで試行
    py -3.13 -m venv backend\venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[setup] py -3.13 が失敗したため python で再試行します..."
        python -m venv backend\venv
        
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[setup] venv 作成に失敗しました。Python 3.13 をインストールしてください。" -ForegroundColor Red
            exit 1
        }
    }
} else {
    Write-Host "[setup] 既存の venv を使用します。"
}

# 仮想環境のアクティベート (PowerShell 用のスクリプトを読み込む)
. .\backend\venv\Scripts\Activate.ps1

# アクティベートが成功したか環境変数で確認
if (-Not $env:VIRTUAL_ENV) {
    Write-Host "[setup] venv のアクティベートに失敗しました。" -ForegroundColor Red
    exit 1
}

Write-Host "[setup] pip を更新します..."
python -m pip install --upgrade pip

Write-Host "[setup] requirements.txt をインストールします..."
pip install -r backend\requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host "[setup] pip install に失敗しました。" -ForegroundColor Red
    exit 1
}

# ディレクトリの作成
if (-Not (Test-Path "captures\processing")) {
    New-Item -ItemType Directory -Path "captures\processing" | Out-Null
}

Write-Host ""
Write-Host "[setup] 完了しました。" -ForegroundColor Green
Write-Host "起動するには:"
Write-Host "   . .\backend\venv\Scripts\Activate.ps1"
Write-Host "   python backend\main.py"
Write-Host "そのあとブラウザで http://localhost:8000 を開いてください。"
