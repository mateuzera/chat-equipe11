@echo off
echo Iniciando Instancia A na porta 8001...
start "Instancia A (8001)" cmd /k "call venv\Scripts\activate && set INSTANCE_ID=Instancia-A && uvicorn app.main:app --port 8001"

echo Iniciando Instancia B na porta 8002...
start "Instancia B (8002)" cmd /k "call venv\Scripts\activate && set INSTANCE_ID=Instancia-B && uvicorn app.main:app --port 8002"

echo As duas instancias foram iniciadas em janelas separadas!