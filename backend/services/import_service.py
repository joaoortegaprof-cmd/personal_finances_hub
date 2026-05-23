"""
ImportService — Importação de extratos bancários OFX/CSV para o FinanceHub.

Fluxo de uso:
    1. parse_ofx() ou parse_csv() → lista de dicts com transações parseadas
    2. detect_duplicates()        → marca cada transação como new/duplicate/possible_duplicate
    3. bulk_import()              → importa as transações confirmadas para o banco

Estratégia de deduplicação:
    - external_id exato (FITID do OFX) → "duplicate" (confiança 100)
    - data + |valor| iguais + description similar → "possible_duplicate" (confiança 70-99)
    - sem correspondência → "new" (confiança 0)
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.transaction import Transaction, TransactionCategory, TransactionType


# ──────────────────────────────────────────────────────────────────────────────
# Helpers internos
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_text(text: str) -> str:
    """Remove espaços extras e converte para minúsculas para comparação fuzzy."""
    return re.sub(r"\s+", " ", text.strip().lower())


def _parse_amount_br(raw: str) -> Decimal:
    """
    Converte string de valor para Decimal aceitando tanto formato BR (1.234,56)
    quanto EN (1234.56). Detecta automaticamente o separador decimal.
    """
    raw = raw.strip().replace("R$", "").replace(" ", "")
    # Se há vírgula e ela parece ser o separador decimal (ex: "1.234,56")
    if "," in raw and "." in raw:
        # Remove o separador de milhar (ponto) e troca vírgula por ponto
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        # Apenas vírgula: é o separador decimal (ex: "1234,56")
        raw = raw.replace(",", ".")
    return Decimal(raw)


def _guess_transaction_type(amount: Decimal) -> TransactionType:
    return TransactionType.INCOME if amount > 0 else TransactionType.DEBIT_EXPENSE


# ──────────────────────────────────────────────────────────────────────────────
# ImportService
# ──────────────────────────────────────────────────────────────────────────────

class ImportService:
    """Serviço de importação de extratos OFX e CSV."""

    # ------------------------------------------------------------------
    # Parsing OFX
    # ------------------------------------------------------------------

    def parse_ofx(self, file_bytes: bytes) -> list[dict[str, Any]]:
        """
        Lê arquivo .ofx ou .qfx e retorna lista de transações no formato interno.

        Campos mapeados:
          date        : data da transação (date)
          amount      : Decimal (positivo=crédito, negativo=débito)
          description : memo ou name do OFX
          type        : "income" se amount > 0, "debit" caso contrário
          external_id : FITID do OFX (para evitar duplicatas)
          source      : "ofx"
        """
        try:
            from ofxparse import OfxParser  # importação lazy — dependência opcional
        except ImportError:
            raise RuntimeError(
                "ofxparse não instalado. Execute: pip install ofxparse"
            )

        # ofxparse espera um objeto file-like
        file_like = io.BytesIO(file_bytes)
        try:
            ofx = OfxParser.parse(file_like)
        except Exception as exc:
            raise ValueError(f"Arquivo OFX inválido: {exc}") from exc

        transactions: list[dict[str, Any]] = []

        for account in (ofx.accounts if hasattr(ofx, "accounts") else []):
            stmts = account.statement if hasattr(account, "statement") else None
            if stmts is None:
                continue
            for tx in (stmts.transactions or []):
                amount = Decimal(str(tx.amount))
                tx_date = tx.date.date() if isinstance(tx.date, datetime) else tx.date
                description = (getattr(tx, "memo", "") or getattr(tx, "payee", "") or "").strip()
                if not description:
                    description = f"Transação {getattr(tx, 'id', '')}"
                transactions.append(
                    {
                        "date":        tx_date,
                        "amount":      amount,
                        "description": description,
                        "type":        _guess_transaction_type(amount).value,
                        "external_id": str(getattr(tx, "id", "") or ""),
                        "category":    TransactionCategory.OTHER.value,
                        "source":      "ofx",
                    }
                )

        # Fallback: tenta parsear como arquivo com um único account (ofxparse API varia)
        if not transactions and hasattr(ofx, "account") and ofx.account:
            stmts = ofx.account.statement
            for tx in (stmts.transactions or []):
                amount = Decimal(str(tx.amount))
                tx_date = tx.date.date() if isinstance(tx.date, datetime) else tx.date
                description = (getattr(tx, "memo", "") or getattr(tx, "payee", "") or "").strip()
                if not description:
                    description = f"Transação {getattr(tx, 'id', '')}"
                transactions.append(
                    {
                        "date":        tx_date,
                        "amount":      amount,
                        "description": description,
                        "type":        _guess_transaction_type(amount).value,
                        "external_id": str(getattr(tx, "id", "") or ""),
                        "category":    TransactionCategory.OTHER.value,
                        "source":      "ofx",
                    }
                )

        return transactions

    # ------------------------------------------------------------------
    # Parsing CSV
    # ------------------------------------------------------------------

    def parse_csv(
        self,
        file_bytes: bytes,
        date_col: str,
        amount_col: str,
        desc_col: str,
        date_format: str = "%d/%m/%Y",
        delimiter: str = ";",
        encoding: str = "utf-8-sig",
    ) -> list[dict[str, Any]]:
        """
        Lê CSV genérico com configuração de colunas.

        Parâmetros:
          file_bytes  : conteúdo bruto do arquivo
          date_col    : nome da coluna de data
          amount_col  : nome da coluna de valor
          desc_col    : nome da coluna de descrição
          date_format : formato strptime (padrão: %d/%m/%Y)
          delimiter   : separador de colunas (padrão: ;)
          encoding    : codificação do arquivo (padrão: utf-8-sig para BOM)
        """
        text_content = file_bytes.decode(encoding, errors="replace")
        reader = csv.DictReader(io.StringIO(text_content), delimiter=delimiter)

        transactions: list[dict[str, Any]] = []
        for i, row in enumerate(reader):
            try:
                # Data
                raw_date = row.get(date_col, "").strip()
                tx_date = datetime.strptime(raw_date, date_format).date()

                # Valor
                raw_amount = row.get(amount_col, "0").strip()
                amount = _parse_amount_br(raw_amount)

                # Descrição
                description = row.get(desc_col, f"Linha {i + 2}").strip() or f"Linha {i + 2}"

                transactions.append(
                    {
                        "date":        tx_date,
                        "amount":      amount,
                        "description": description,
                        "type":        _guess_transaction_type(amount).value,
                        "external_id": f"csv_{i}_{raw_date}_{raw_amount}",
                        "category":    TransactionCategory.OTHER.value,
                        "source":      "csv",
                    }
                )
            except (ValueError, InvalidOperation, KeyError) as exc:
                # Registra como erro mas não interrompe o parsing
                transactions.append(
                    {
                        "date":        date.today(),
                        "amount":      Decimal("0"),
                        "description": f"[ERRO linha {i + 2}: {exc}]",
                        "type":        TransactionType.DEBIT_EXPENSE.value,
                        "external_id": f"err_{i}",
                        "category":    TransactionCategory.OTHER.value,
                        "source":      "csv",
                        "parse_error": str(exc),
                    }
                )

        return transactions

    # ------------------------------------------------------------------
    # Detecção de duplicatas
    # ------------------------------------------------------------------

    async def detect_duplicates(
        self,
        transactions: list[dict[str, Any]],
        account_id: int,
        db: AsyncSession,
    ) -> list[dict[str, Any]]:
        """
        Compara cada transação importada com as existentes no banco.

        Critérios:
          1. external_id exato → "duplicate" (confiança 100)
          2. data + |amount| iguais + description similar (>60%) → "possible_duplicate"
          3. Sem correspondência → "new"

        Retorna a lista original enriquecida com:
          status     : "new" | "duplicate" | "possible_duplicate"
          confidence : 0 (new) | 70-99 (possible) | 100 (exact)
          selected   : True para "new" e "possible_duplicate", False para "duplicate"
        """
        # Carrega transações existentes da conta no banco
        result = await db.execute(
            select(Transaction).where(Transaction.account_id == account_id)
        )
        existing: list[Transaction] = list(result.scalars().all())

        # Conjuntos para busca rápida
        existing_external_ids: set[str] = {
            (tx.tags or "").split("fitid:")[-1].split(",")[0].strip()
            for tx in existing
            if tx.tags and "fitid:" in tx.tags
        }
        # Também busca por external_id armazenado nos notes
        existing_ext_from_notes: set[str] = {
            tx.notes.split("extid:")[-1].split()[0].strip()
            for tx in existing
            if tx.notes and "extid:" in tx.notes
        }
        existing_external_ids |= existing_ext_from_notes

        # Índice por (data, |amount|) para busca rápida de possíveis duplicatas
        existing_by_date_amount: dict[tuple[date, Decimal], list[str]] = {}
        for tx in existing:
            key = (tx.transaction_date, abs(tx.amount))
            existing_by_date_amount.setdefault(key, []).append(
                _normalize_text(tx.description)
            )

        result_list: list[dict[str, Any]] = []
        for tx in transactions:
            tx_copy = dict(tx)
            ext_id = tx_copy.get("external_id", "")
            amount = tx_copy.get("amount", Decimal("0"))
            tx_date = tx_copy.get("date", date.today())
            description = _normalize_text(tx_copy.get("description", ""))

            # 1. External ID exato
            if ext_id and ext_id in existing_external_ids:
                tx_copy["status"]     = "duplicate"
                tx_copy["confidence"] = 100
                tx_copy["selected"]   = False
                result_list.append(tx_copy)
                continue

            # 2. Data + |amount| + similarity de descrição
            key = (tx_date, abs(amount))
            existing_descs = existing_by_date_amount.get(key, [])
            best_similarity = 0.0
            for ex_desc in existing_descs:
                sim = _description_similarity(description, ex_desc)
                best_similarity = max(best_similarity, sim)

            if best_similarity >= 0.7:
                confidence = int(best_similarity * 100)
                tx_copy["status"]     = "possible_duplicate"
                tx_copy["confidence"] = confidence
                tx_copy["selected"]   = True  # marcada mas com aviso
            else:
                tx_copy["status"]     = "new"
                tx_copy["confidence"] = 0
                tx_copy["selected"]   = True

            result_list.append(tx_copy)

        return result_list

    # ------------------------------------------------------------------
    # Importação em lote
    # ------------------------------------------------------------------

    async def bulk_import(
        self,
        transactions: list[dict[str, Any]],
        account_id: int,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """
        Importa as transações confirmadas para o banco.

        Cada transação deve ter os campos do formato interno.
        Armazena o external_id no campo tags como "fitid:XXXXX".

        Retorna:
          imported : número de transações criadas
          skipped  : número ignoradas (already existing)
          errors   : lista de erros com descrição
        """
        imported = 0
        skipped  = 0
        errors: list[str] = []

        for tx in transactions:
            try:
                # Valida data
                tx_date = tx["date"]
                if isinstance(tx_date, str):
                    tx_date = datetime.fromisoformat(tx_date).date()

                # Valor sempre positivo
                amount = abs(Decimal(str(tx["amount"])))
                if amount == 0:
                    skipped += 1
                    continue

                # Tipo de transação
                try:
                    tx_type = TransactionType(tx.get("type", "debit"))
                except ValueError:
                    tx_type = TransactionType.DEBIT_EXPENSE

                # Categoria
                try:
                    category = TransactionCategory(tx.get("category", "outros"))
                except ValueError:
                    category = TransactionCategory.OTHER

                # Armazena external_id no campo tags para deduplicação futura
                ext_id = tx.get("external_id", "")
                tags = f"fitid:{ext_id}" if ext_id else None

                new_tx = Transaction(
                    description=tx.get("description", "Importado"),
                    amount=amount,
                    transaction_date=tx_date,
                    transaction_type=tx_type,
                    category=category,
                    account_id=account_id,
                    notes=tx.get("notes"),
                    tags=tags,
                )
                db.add(new_tx)
                imported += 1

            except Exception as exc:
                errors.append(
                    f"Linha '{tx.get('description', '?')}': {exc}"
                )

        if imported > 0:
            await db.commit()

        return {
            "imported": imported,
            "skipped":  skipped,
            "errors":   errors,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Similaridade de strings (Jaccard sobre bigramas)
# ──────────────────────────────────────────────────────────────────────────────

def _description_similarity(a: str, b: str) -> float:
    """
    Calcula similaridade entre dois textos usando bigramas Jaccard.

    Retorna valor entre 0.0 (completamente diferente) e 1.0 (idêntico).
    Rápido e suficiente para comparação de descrições bancárias.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    def _bigrams(text: str) -> set[str]:
        return {text[i: i + 2] for i in range(len(text) - 1)}

    bg_a = _bigrams(a)
    bg_b = _bigrams(b)
    intersection = len(bg_a & bg_b)
    union = len(bg_a | bg_b)
    return intersection / union if union > 0 else 0.0
