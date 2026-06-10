# ============================================
# wc2026-copiloto — Setup Computadora 2
# Ejecutar UNA SOLA VEZ en la nueva PC
# ============================================
Write-Host "=== wc2026-copiloto Setup PC2 ===" -ForegroundColor Cyan

# 1. Instalar dependencias
Write-Host "`n[1/4] Instalando dependencias..." -ForegroundColor Yellow
pip install "fastapi[standard]" aiofiles requests --quiet
if ($LASTEXITCODE -ne 0) { Write-Host "Error instalando dependencias" -ForegroundColor Red; exit 1 }

# 2. API Key
Write-Host "`n[2/4] Configurar ODDS_API_KEY" -ForegroundColor Yellow
Write-Host "Ingresa tu ODDS_API_KEY (se guarda para esta sesion):"
$apiKey = Read-Host
$env:ODDS_API_KEY = $apiKey
Write-Host "Key configurada para esta sesion." -ForegroundColor Green
Write-Host "Para hacerla permanente, agregarla a las variables de entorno del sistema."

# 3. Git pull para obtener la BD actualizada
Write-Host "`n[3/4] Obteniendo datos actualizados de GitHub..." -ForegroundColor Yellow
git pull origin main
if ($LASTEXITCODE -ne 0) { Write-Host "Warning: git pull fallo. Verifica tu conexion." -ForegroundColor Yellow }

# 4. Si no existe wc2026.db, hacer seed inicial
if (-Not (Test-Path "wc2026.db")) {
    Write-Host "`n[4/4] Base de datos no encontrada. Ejecutando seed inicial..." -ForegroundColor Yellow
    python backend/scripts/seed.py
    python backend/scripts/init_team_strength.py
    python backend/scripts/run_predictions.py
} else {
    Write-Host "`n[4/4] Base de datos encontrada en repo. Lista para usar." -ForegroundColor Green
}

Write-Host "`n=== Setup completado ===" -ForegroundColor Cyan
Write-Host "Para iniciar el dashboard: python main.py" -ForegroundColor Green
Write-Host "Luego abrir: http://localhost:8000" -ForegroundColor Green
