"""
Smoke test dos quatro serviços do FinanceHub.

Verifica que cada serviço instancia corretamente, executa sem exceções
e retorna os tipos e valores esperados — usando SQLite em memória com
dados controlados inseridos no setUp.

Não substitui testes unitários completos (ver tests/): o objetivo é
detectar erros de importação, quebras de contrato de tipo e falhas de
integração básica o mais rápido possível, em qualquer ambiente.

Execução:
    cd /caminho/do/projeto
    source finances/bin/activate   # ou o nome do seu venv
    python scripts/smoke_test_services.py
"""

import asyncio
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

# Garante que os imports do pacote backend funcionem a partir da raiz do projeto
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.core.database import Base
from backend.models.account import Account, AccountType, CreditCard, CreditCardInvoice, InvoiceStatus
from backend.models.asset import Asset, AssetIndexer, AssetPosition, AssetType, LiquidityWindow, OperationType
from backend.models.transaction import Transaction, TransactionCategory, TransactionType
from backend.services.financial_summary_service import FinancialSummaryService
from backend.services.liquidity_service import LiquidityService
from backend.services.alert_service import AlertService, AlertType
from backend.services.market_data_service import MarketDataService

# Cores ANSI para output legível no terminal
GREEN = "\033[92m"
RED   = "\033[91m"
YELLOW = "\033[93m"
BOLD  = "\033[1m"
RESET = "\033[0m"

passed = 0
failed = 0


def ok(label: str, detail: str = "") -> None:
    global passed
    passed += 1
    suffix = f"  → {detail}" if detail else ""
    print(f"  {GREEN}✓{RESET} {label}{suffix}")


def fail(label: str, err: Exception) -> None:
    global failed
    failed += 1
    print(f"  {RED}✗ {label}{RESET}")
    print(f"    {RED}Erro: {err}{RESET}")


def warn(label: str, reason: str) -> None:
    print(f"  {YELLOW}⚠{RESET} {label}  ({reason})")


# -----------------------------------------------------------------
# Setup: banco em memória com dados controlados
# -----------------------------------------------------------------

async def build_test_db() -> async_sessionmaker[AsyncSession]:
    """
    Cria um banco SQLite em memória e popula com um cenário mínimo:

      • 1 conta corrente com saldo inicial de R$5.000
      • 3 transações no mês corrente: salário R$8.000, aluguel R$2.000, mercado R$800
      • 1 cartão de crédito com fatura de R$1.500 vencendo amanhã (deve gerar alerta)
      • 1 ação (PETR4): 100 cotas compradas a R$35 (D+2)
      • 1 CDB: R$10.000 com vencimento em 15 dias (deve gerar alerta de renda fixa)
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    today = date.today()
    month_start = today.replace(day=1)

    async with factory() as s:
        # ── Conta corrente ──────────────────────────────────────────────
        account = Account(
            name="Conta Principal",
            bank_name="Banco Teste",
            account_type=AccountType.CHECKING,
            initial_balance=Decimal("5000.00"),
            initial_balance_date=month_start - timedelta(days=30),
        )
        s.add(account)
        await s.flush()

        # ── Transações do mês ──────────────────────────────────────────
        s.add_all([
            Transaction(
                description="Salário maio",
                amount=Decimal("8000.00"),
                transaction_date=month_start,
                transaction_type=TransactionType.INCOME,
                category=TransactionCategory.SALARY,
                account_id=account.id,
            ),
            Transaction(
                description="Aluguel",
                amount=Decimal("2000.00"),
                transaction_date=month_start + timedelta(days=5),
                transaction_type=TransactionType.EXPENSE,
                category=TransactionCategory.HOUSING,
                account_id=account.id,
            ),
            Transaction(
                description="Supermercado",
                amount=Decimal("800.00"),
                transaction_date=month_start + timedelta(days=10),
                transaction_type=TransactionType.EXPENSE,
                category=TransactionCategory.SUPERMARKET,
                account_id=account.id,
            ),
        ])

        # ── Cartão com fatura vencendo amanhã ──────────────────────────
        due_day = (today + timedelta(days=1)).day
        card = CreditCard(
            name="Nubank Roxinho",
            bank_name="Nubank",
            last_four_digits="9999",
            credit_limit=Decimal("5000.00"),
            closing_day=5,
            due_day=min(due_day, 28),
            payment_account_id=account.id,
        )
        s.add(card)
        await s.flush()

        tomorrow = today + timedelta(days=1)
        s.add(CreditCardInvoice(
            credit_card_id=card.id,
            month=tomorrow.month,
            year=tomorrow.year,
            closing_date=tomorrow - timedelta(days=5),
            due_date=tomorrow,
            total_amount=Decimal("1500.00"),
            status=InvoiceStatus.CLOSED,
        ))

        # ── Ação: PETR4 (D+2) ─────────────────────────────────────────
        stock = Asset(
            name="Petrobras PN",
            ticker="PETR4",
            asset_type=AssetType.STOCK,
            liquidity=LiquidityWindow.D2,
        )
        s.add(stock)
        await s.flush()

        s.add(AssetPosition(
            asset_id=stock.id,
            account_id=account.id,
            operation_date=month_start - timedelta(days=60),
            quantity=Decimal("100"),
            unit_price=Decimal("35.00"),
            fees=Decimal("5.00"),
            operation_type=OperationType.BUY,
        ))

        # ── CDB vencendo em 15 dias (D+Vencimento) ────────────────────
        cdb = Asset(
            name="CDB Banco Alfa 120% CDI",
            asset_type=AssetType.FIXED_INCOME,
            indexer=AssetIndexer.CDI,
            liquidity=LiquidityWindow.MATURITY,
            maturity_date=today + timedelta(days=15),
        )
        s.add(cdb)
        await s.flush()

        s.add(AssetPosition(
            asset_id=cdb.id,
            account_id=account.id,
            operation_date=month_start - timedelta(days=90),
            quantity=Decimal("1"),
            unit_price=Decimal("10000.00"),
            fees=Decimal("0.00"),
            operation_type=OperationType.BUY,
        ))

        await s.commit()

    return factory


# -----------------------------------------------------------------
# Testes dos serviços
# -----------------------------------------------------------------

async def test_financial_summary(factory: async_sessionmaker) -> None:
    print(f"\n{BOLD}FinancialSummaryService{RESET}")

    async with factory() as s:
        svc = FinancialSummaryService(s)

        # ── get_monthly_summary ────────────────────────────────────────
        try:
            m = await svc.get_monthly_summary()
            assert m.income == Decimal("8000.00"), f"income {m.income} ≠ 8000"
            assert m.expense == Decimal("2800.00"), f"expense {m.expense} ≠ 2800"
            assert m.balance == Decimal("5200.00"), f"balance {m.balance} ≠ 5200"
            assert m.savings_rate == Decimal("65.00"), f"savings_rate {m.savings_rate} ≠ 65"
            ok("get_monthly_summary",
               f"receita={m.income} despesa={m.expense} saldo={m.balance} poupança={m.savings_rate}%")
        except Exception as e:
            fail("get_monthly_summary", e)

        # ── get_net_worth ──────────────────────────────────────────────
        try:
            nw = await svc.get_net_worth()
            assert nw.total_assets > 0, "total_assets deve ser positivo"
            assert nw.total_liabilities == Decimal("1500.00"), \
                f"liabilities {nw.total_liabilities} ≠ 1500"
            assert nw.net_worth == nw.total_assets - nw.total_liabilities
            ok("get_net_worth",
               f"ativos={nw.total_assets} passivos={nw.total_liabilities} líquido={nw.net_worth}")
        except Exception as e:
            fail("get_net_worth", e)

        # ── get_health_score ───────────────────────────────────────────
        try:
            h = await svc.get_health_score()
            assert 0 <= h.total <= 100, f"score {h.total} fora do range 0-100"
            assert h.savings_component >= 0
            assert h.emergency_fund_component >= 0
            assert h.debt_component >= 0
            assert h.savings_component + h.emergency_fund_component + h.debt_component == h.total
            ok("get_health_score",
               f"total={h.total}/100 (poupança={h.savings_component} "
               f"fundo={h.emergency_fund_component} dívida={h.debt_component})")
        except Exception as e:
            fail("get_health_score", e)


async def test_liquidity(factory: async_sessionmaker) -> None:
    print(f"\n{BOLD}LiquidityService{RESET}")

    async with factory() as s:
        svc = LiquidityService(s)

        # ── get_liquidity_breakdown ────────────────────────────────────
        try:
            bd = await svc.get_liquidity_breakdown()
            # Conta corrente (5000 inicial + 8000 - 2000 - 800 = 10200) → D+0
            assert bd.d0_value > 0, "D+0 deve incluir o saldo da conta"
            # PETR4 100 × R$35 = R$3.500 → D+2
            assert bd.d2_value > 0, "D+2 deve incluir a posição em PETR4"
            # CDB R$10.000 → vencimento
            assert bd.maturity_value > 0, "Vencimento deve incluir o CDB"
            assert bd.total_portfolio >= bd.total_liquid
            assert bd.total_liquid == bd.d0_value + bd.d1_value + bd.d2_value
            ok("get_liquidity_breakdown",
               f"D+0={bd.d0_value:.2f} D+2={bd.d2_value:.2f} "
               f"venc={bd.maturity_value:.2f} total={bd.total_portfolio:.2f}")
        except Exception as e:
            fail("get_liquidity_breakdown", e)

        # ── get_emergency_fund_coverage ────────────────────────────────
        try:
            cov = await svc.get_emergency_fund_coverage(
                monthly_expense=Decimal("2800.00"), target_months=6
            )
            assert "current_liquid" in cov
            assert "coverage_months" in cov
            assert cov["target_value"] == Decimal("2800.00") * 6
            ok("get_emergency_fund_coverage",
               f"cobre {cov['coverage_months']}m de {6}m (progresso {cov['progress_pct']}%)")
        except Exception as e:
            fail("get_emergency_fund_coverage", e)


async def test_alerts(factory: async_sessionmaker) -> None:
    print(f"\n{BOLD}AlertService{RESET}")

    async with factory() as s:
        svc = AlertService(s)

        # ── get_all_alerts — deve gerar pelo menos fatura + vencimento CDB ──
        try:
            alerts = await svc.get_all_alerts(invoice_due_days=3, maturity_days_ahead=30)
            types = {a.alert_type for a in alerts}
            assert AlertType.INVOICE_DUE in types, \
                "Esperava alerta de fatura vencendo amanhã"
            assert AlertType.FIXED_INCOME in types, \
                "Esperava alerta de CDB vencendo em 15 dias"
            ok(f"get_all_alerts", f"{len(alerts)} alerta(s) gerado(s)")
            for a in alerts:
                print(f"    [{a.priority.value.upper():6}] {a.title}")
        except Exception as e:
            fail("get_all_alerts", e)

        # ── check_darf — sem vendas de ações no mês → nenhum alerta ──
        try:
            darf = await svc.check_darf()
            assert darf == [], f"Esperava lista vazia, got {darf}"
            ok("check_darf", "0 alerta(s) — sem vendas de ações no mês")
        except Exception as e:
            fail("check_darf", e)

        # ── check_invoice_due — fatura vencendo amanhã → 1 alerta ─────
        try:
            inv = await svc.check_invoice_due(days_ahead=3)
            assert len(inv) >= 1, f"Esperava ≥1 alerta de fatura, got {len(inv)}"
            ok("check_invoice_due", f"{len(inv)} alerta(s) de fatura")
        except Exception as e:
            fail("check_invoice_due", e)

        # ── check_fixed_income_maturity — CDB em 15 dias → 1 alerta ──
        try:
            mat = await svc.check_fixed_income_maturity(days_ahead=30)
            assert len(mat) >= 1, f"Esperava ≥1 alerta de vencimento, got {len(mat)}"
            ok("check_fixed_income_maturity",
               f"{len(mat)} alerta(s) | {mat[0].data['days_to_maturity']} dias para vencimento")
        except Exception as e:
            fail("check_fixed_income_maturity", e)

        # ── check_savings_rate com meta de 80% → 1 alerta (taxa=65%) ──
        try:
            sr = await svc.check_savings_rate(goal_pct=Decimal("80"))
            assert len(sr) == 1, f"Esperava 1 alerta (65% < 80%), got {len(sr)}"
            assert sr[0].data["savings_rate_pct"] == 65.0
            ok("check_savings_rate (meta 80%)", f"{sr[0].message[:60]}…")
        except Exception as e:
            fail("check_savings_rate (meta 80%)", e)

        # ── check_savings_rate com meta de 50% → sem alerta (taxa=65%) ─
        try:
            sr2 = await svc.check_savings_rate(goal_pct=Decimal("50"))
            assert sr2 == [], f"65% ≥ 50%: não deveria alertar, got {sr2}"
            ok("check_savings_rate (meta 50%)", "sem alerta — meta atingida")
        except Exception as e:
            fail("check_savings_rate (meta 50%)", e)


async def test_market_data() -> None:
    print(f"\n{BOLD}MarketDataService{RESET}")

    svc = MarketDataService()

    # ── invalidate_cache ────────────────────────────────────────────
    try:
        svc.invalidate_cache()
        ok("invalidate_cache", "cache limpo")
    except Exception as e:
        fail("invalidate_cache", e)

    # ── to_yfinance_ticker ──────────────────────────────────────────
    try:
        cases = [
            ("PETR4",   "PETR4.SA"),
            ("HGLG11",  "HGLG11.SA"),
            ("^BVSP",   "^BVSP"),
            ("^GSPC",   "^GSPC"),
            ("BTC-USD", "BTC-USD"),
            ("PETR4.SA","PETR4.SA"),
        ]
        for raw, expected in cases:
            result = svc.to_yfinance_ticker(raw)
            assert result == expected, f"{raw} → {result} (esperado {expected})"
        ok("to_yfinance_ticker", "6/6 conversões corretas")
    except Exception as e:
        fail("to_yfinance_ticker", e)

    # ── _calculate_return ───────────────────────────────────────────
    try:
        cases = [
            ([Decimal("100"), Decimal("110"), Decimal("121")], Decimal("21.00")),
            ([Decimal("200"), Decimal("100")],                 Decimal("-50.00")),
            ([Decimal("0"), Decimal("100")],                   Decimal("0.00")),   # divisão por zero
            ([Decimal("50")],                                  Decimal("0.00")),   # série com 1 elemento
        ]
        for prices, expected in cases:
            result = svc._calculate_return(prices)
            assert result == expected, f"prices={prices} → {result} (esperado {expected})"
        ok("_calculate_return", "4/4 cálculos corretos")
    except Exception as e:
        fail("_calculate_return", e)

    # ── get_quote — requer conexão com internet ─────────────────────
    try:
        quote = await svc.get_quote("PETR4.SA")
        assert quote.price >= 0
        assert quote.ticker == "PETR4.SA"
        assert quote.currency != ""
        ok("get_quote(PETR4.SA)", f"R${quote.price} ({quote.fetched_at.strftime('%H:%M')})")

        # Segunda chamada deve vir do cache (sem nova requisição)
        quote2 = await svc.get_quote("PETR4.SA")
        assert quote2.price == quote.price, "cache deve retornar o mesmo preço"
        ok("get_quote cache hit", f"preço idêntico ({quote2.price})")
    except Exception as e:
        warn("get_quote(PETR4.SA)", f"necessita internet — {e}")

    # ── get_price_history ───────────────────────────────────────────
    try:
        start = date.today() - timedelta(days=30)
        hist = await svc.get_price_history("PETR4.SA", start_date=start)
        assert isinstance(hist.prices, list)
        assert len(hist.dates) == len(hist.prices)
        ok("get_price_history(PETR4.SA, 30d)",
           f"{len(hist.prices)} pregão(ões) | último: R${hist.prices[-1] if hist.prices else 'N/D'}")
    except Exception as e:
        warn("get_price_history", f"necessita internet — {e}")


# -----------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------

async def main() -> None:
    print(f"\n{BOLD}{'=' * 52}")
    print("  FinanceHub — Smoke Test dos Serviços")
    print(f"{'=' * 52}{RESET}")

    factory = await build_test_db()
    print(f"\n  {GREEN}Banco em memória criado e populado{RESET}")

    await test_financial_summary(factory)
    await test_liquidity(factory)
    await test_alerts(factory)
    await test_market_data()

    # ── Resultado final ───────────────────────────────────────────
    print(f"\n{BOLD}{'=' * 52}")
    color = GREEN if failed == 0 else RED
    print(f"  Resultado: {GREEN}{passed} passou(ram){RESET}  /  {color}{failed} falhou(ram){RESET}")
    print(f"{BOLD}{'=' * 52}{RESET}\n")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
