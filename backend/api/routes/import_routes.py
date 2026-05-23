"""
Endpoints de importação de extratos bancários — OFX e CSV.

Fluxo em 2 passos:
    1. POST /import/preview  → parse + detecção de duplicatas (sem gravar no banco)
    2. POST /import/confirm  → grava as transações selecionadas

Atalhos de formato:
    POST /import/ofx  → equivale a preview com format=ofx
    POST /import/csv  → equivale a preview com format=csv + configuração de colunas
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.database import get_db
from backend.services.import_service import ImportService

router = APIRouter(prefix="/import", tags=["Importação"])

_service = ImportService()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _serialize_tx(tx: dict[str, Any]) -> dict[str, Any]:
    """Converte tipos Python não-JSON-serializáveis para primitivos."""
    out = {}
    for k, v in tx.items():
        if isinstance(v, date):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = float(v)
        else:
            out[k] = v
    return out


# ─────────────────────────────────────────────────────────────────────────────
# POST /import/ofx
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/ofx", summary="Pré-visualizar importação de arquivo OFX")
async def import_ofx_preview(
    file:       UploadFile,
    account_id: Annotated[int, Form()],
    db:         AsyncSession = Depends(get_db),
):
    """
    Faz o parse de um arquivo OFX/QFX e retorna as transações com status de
    duplicata para revisão antes da importação.

    Não grava nada no banco — apenas analisa.
    """
    content = await file.read()
    try:
        transactions = _service.parse_ofx(content)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    tagged = await _service.detect_duplicates(transactions, account_id, db)
    return {
        "total":      len(tagged),
        "new":        sum(1 for t in tagged if t["status"] == "new"),
        "duplicates": sum(1 for t in tagged if t["status"] == "duplicate"),
        "possible":   sum(1 for t in tagged if t["status"] == "possible_duplicate"),
        "transactions": [_serialize_tx(t) for t in tagged],
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /import/csv
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/csv", summary="Pré-visualizar importação de arquivo CSV")
async def import_csv_preview(
    file:        UploadFile,
    account_id:  Annotated[int,  Form()],
    date_col:    Annotated[str,  Form()],
    amount_col:  Annotated[str,  Form()],
    desc_col:    Annotated[str,  Form()],
    date_format: Annotated[str,  Form()] = "%d/%m/%Y",
    delimiter:   Annotated[str,  Form()] = ";",
    db:          AsyncSession = Depends(get_db),
):
    """
    Faz o parse de um CSV genérico configurável e retorna as transações com
    status de duplicata para revisão.

    Não grava nada no banco — apenas analisa.
    """
    content = await file.read()
    try:
        transactions = _service.parse_csv(
            content,
            date_col=date_col,
            amount_col=amount_col,
            desc_col=desc_col,
            date_format=date_format,
            delimiter=delimiter,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    tagged = await _service.detect_duplicates(transactions, account_id, db)
    return {
        "total":      len(tagged),
        "new":        sum(1 for t in tagged if t["status"] == "new"),
        "duplicates": sum(1 for t in tagged if t["status"] == "duplicate"),
        "possible":   sum(1 for t in tagged if t["status"] == "possible_duplicate"),
        "transactions": [_serialize_tx(t) for t in tagged],
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /import/preview  (genérico)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/preview", summary="Pré-visualização genérica (OFX ou CSV)")
async def import_preview(
    file:        UploadFile,
    account_id:  Annotated[int,  Form()],
    format:      Annotated[str,  Form()] = "ofx",  # "ofx" | "csv"
    date_col:    Annotated[str,  Form()] = "Data",
    amount_col:  Annotated[str,  Form()] = "Valor",
    desc_col:    Annotated[str,  Form()] = "Descrição",
    date_format: Annotated[str,  Form()] = "%d/%m/%Y",
    delimiter:   Annotated[str,  Form()] = ";",
    db:          AsyncSession = Depends(get_db),
):
    """
    Endpoint genérico que aceita OFX ou CSV dependendo do parâmetro `format`.
    Útil para o wizard de importação do frontend.
    """
    content = await file.read()
    try:
        if format.lower() in ("ofx", "qfx"):
            transactions = _service.parse_ofx(content)
        else:
            transactions = _service.parse_csv(
                content,
                date_col=date_col,
                amount_col=amount_col,
                desc_col=desc_col,
                date_format=date_format,
                delimiter=delimiter,
            )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    tagged = await _service.detect_duplicates(transactions, account_id, db)
    return {
        "total":      len(tagged),
        "new":        sum(1 for t in tagged if t["status"] == "new"),
        "duplicates": sum(1 for t in tagged if t["status"] == "duplicate"),
        "possible":   sum(1 for t in tagged if t["status"] == "possible_duplicate"),
        "transactions": [_serialize_tx(t) for t in tagged],
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /import/confirm
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/confirm", summary="Confirmar importação de transações")
async def import_confirm(
    payload: dict[str, Any],
    db:      AsyncSession = Depends(get_db),
):
    """
    Recebe a lista de transações selecionadas (após revisão no wizard) e
    as importa para o banco de dados.

    Body JSON:
        {
            "account_id": 1,
            "transactions": [
                {
                    "date": "2026-01-15",
                    "amount": -250.00,
                    "description": "Supermercado",
                    "type": "debit",
                    "category": "supermercado",
                    "external_id": "FIT20260115001"
                },
                ...
            ]
        }
    """
    account_id = payload.get("account_id")
    transactions = payload.get("transactions", [])

    if not account_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="account_id é obrigatório.",
        )
    if not transactions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Nenhuma transação fornecida para importação.",
        )

    # Converte datas string → date
    for tx in transactions:
        if isinstance(tx.get("date"), str):
            tx["date"] = date.fromisoformat(tx["date"])
        if isinstance(tx.get("amount"), (int, float)):
            tx["amount"] = Decimal(str(tx["amount"]))

    result = await _service.bulk_import(transactions, account_id, db)

    if result["imported"] == 0 and result["errors"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Nenhuma transação importada.", "errors": result["errors"]},
        )

    return result
