#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# FinanceHub — Executar suite de testes
#
# Uso:
#   ./scripts/run_tests.sh           → roda todos os testes com cobertura
#   ./scripts/run_tests.sh unit      → apenas testes unitários
#   ./scripts/run_tests.sh integration → apenas testes de integração
#   ./scripts/run_tests.sh -k nome   → filtra por nome de teste
#
# Saída de cobertura: terminal + htmlcov/index.html
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Detecta o Python correto: prefere anaconda, cai de volta no venv
if [ -f "$PROJECT_ROOT/finances/bin/python" ]; then
    PYTHON="$PROJECT_ROOT/finances/bin/python"
elif [ -f "/home/joao_ortega/anaconda3/bin/python" ]; then
    PYTHON="/home/joao_ortega/anaconda3/bin/python"
else
    PYTHON="python3"
fi

echo "🐍 Python: $PYTHON"
echo "📁 Projeto: $PROJECT_ROOT"
echo ""

# Define o subdiretório de testes conforme o primeiro argumento
case "${1:-all}" in
    unit)
        TEST_PATH="tests/unit/"
        LABEL="unitários"
        ;;
    integration)
        TEST_PATH="tests/integration/"
        LABEL="de integração"
        ;;
    all|*)
        TEST_PATH="tests/"
        LABEL="completos"
        ;;
esac

# Repassa argumentos extras para o pytest (ex: -k nome_do_teste)
EXTRA_ARGS="${@:2}"

echo "🧪 Rodando testes $LABEL..."
echo ""

$PYTHON -m pytest \
    "$TEST_PATH" \
    -v \
    --tb=short \
    --asyncio-mode=auto \
    --cov=backend \
    --cov-report=term-missing \
    --cov-report=html:htmlcov \
    $EXTRA_ARGS

echo ""
echo "✅ Testes concluídos."
echo "📊 Relatório de cobertura: htmlcov/index.html"
