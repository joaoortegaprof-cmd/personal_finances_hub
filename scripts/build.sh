#!/bin/bash
set -e

echo "======================================"
echo "  FinanceHub — Build para Linux"
echo "======================================"

cd "$(dirname "$0")/.."
ROOT_DIR="$(pwd)"

if [ ! -d "finances" ]; then
    echo "ERRO: ambiente virtual 'finances' não encontrado"
    echo "Execute: python -m venv finances"
    exit 1
fi

source finances/bin/activate

pip install pyinstaller --quiet
echo "✓ PyInstaller disponível"

echo "Limpando builds anteriores..."
rm -rf build/ dist/
echo "✓ Limpo"

if [ -f "data/financehub.db" ]; then
    python scripts/backup.py
    echo "✓ Backup do banco criado"
fi

echo ""
echo "Gerando executável (pode demorar 3-5 min)..."
pyinstaller finance-hub.spec \
    --clean \
    --noconfirm \
    --log-level WARN

if [ ! -f "dist/FinanceHub/FinanceHub" ]; then
    echo "ERRO: executável não foi gerado"
    exit 1
fi

echo "Copiando estrutura de dados..."
mkdir -p dist/FinanceHub/data/logs
mkdir -p dist/FinanceHub/data/backups

SIZE=$(du -sh dist/FinanceHub | cut -f1)
echo ""
echo "======================================"
echo "  Build concluído com sucesso!"
echo "  Tamanho: $SIZE"
echo "  Local: dist/FinanceHub/FinanceHub"
echo ""
echo "  Para testar:"
echo "  ./dist/FinanceHub/FinanceHub"
echo "======================================"
