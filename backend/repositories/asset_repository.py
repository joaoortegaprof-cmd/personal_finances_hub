"""
Repositório de ativos e posições de investimento.
"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models.asset import (
    Asset,
    AssetPosition,
    AssetType,
    LiquidityWindow,
    OperationType,
)
from backend.repositories.base import BaseRepository


class AssetRepository(BaseRepository[Asset]):
    """
    Operações de banco para ativos e histórico de operações.

    Herda get_by_id, get_all, create, update, delete e exists do BaseRepository.

    Nota sobre cálculo de preço médio:
        O método correto no Brasil é o Custo Médio Ponderado (CMP), que
        recalcula o preço médio a cada compra e preserva o custo ao vender.
        O cálculo iterativo preciso deve ser feito na camada de serviço com
        todos os eventos em ordem cronológica. Os métodos aqui fornecem
        os dados brutos agregados — rápidos e úteis para uma aproximação.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Asset, session)

    # -----------------------------------------------------------------
    # Listagem por tipo
    # -----------------------------------------------------------------

    async def get_by_type(self, asset_type: AssetType) -> list[Asset]:
        """
        Lista todos os ativos de uma classe (ação, FII, cripto etc.),
        ordenados por ticker quando disponível, depois por nome.

        func.coalesce(ticker, name) garante que ativos sem ticker
        (como CDBs) apareçam no final da ordenação por ticker.
        """
        result = await self._session.execute(
            select(Asset)
            .where(Asset.asset_type == asset_type)
            .order_by(func.coalesce(Asset.ticker, Asset.name))
        )
        return list(result.scalars().all())

    # -----------------------------------------------------------------
    # Posição consolidada (quantidade líquida)
    # -----------------------------------------------------------------

    async def get_consolidated_position(self, asset_id: int) -> Decimal:
        """
        Retorna a quantidade líquida atual do ativo:

            posição = SUM(quantity de todas as operações)

        Compras têm quantity positiva; vendas têm quantity negativa.
        Operações com quantity=0 (ajustes zerados) são ignoradas.
        Retorna 0 se não houver operações ou se o ativo foi totalmente vendido.
        """
        result = await self._session.execute(
            select(
                func.coalesce(func.sum(AssetPosition.quantity), Decimal("0"))
            ).where(
                AssetPosition.asset_id == asset_id,
                AssetPosition.quantity != 0,  # ignora ajustes zerados
            )
        )
        return result.scalar_one() or Decimal("0")

    # -----------------------------------------------------------------
    # Preço médio ponderado (aproximação)
    # -----------------------------------------------------------------

    async def calculate_avg_price(self, asset_id: int) -> Decimal:
        """
        Preço médio ponderado das COMPRAS reais do ativo (CMP aproximado).

        Fórmula:
            preço_médio = SUM(qty × price) / SUM(qty)
            apenas para operações BUY com quantity > 0.

        Ignora: vendas, ajustes com qty=0 e operações com qty negativa.
        Retorna 0 se não houver compras reais.
        """
        result = await self._session.execute(
            select(
                func.coalesce(
                    func.sum(AssetPosition.quantity * AssetPosition.unit_price),
                    Decimal("0"),
                ).label("total_cost"),
                func.coalesce(
                    func.sum(AssetPosition.quantity),
                    Decimal("0"),
                ).label("total_qty"),
            ).where(
                AssetPosition.asset_id == asset_id,
                AssetPosition.operation_type == OperationType.BUY,
                AssetPosition.quantity > 0,  # apenas compras reais
            )
        )
        row = result.one()
        total_cost: Decimal = row.total_cost
        total_qty: Decimal = row.total_qty

        if not total_qty or total_qty == 0:
            return Decimal("0")

        return (Decimal(str(total_cost)) / Decimal(str(total_qty))).quantize(Decimal("0.000001"))

    async def get_asset_current_value(
        self,
        asset_id: int,
        current_price: Decimal | None = None,
    ) -> dict:
        """
        Calcula valor atual, custo investido e rentabilidade de um ativo.

        Se ``current_price`` for informado, usa qty × current_price como
        valor de mercado.  Caso contrário, usa qty × avg_price (custo).

        Retorna dict com os campos usados na exibição da tabela de posições.
        """
        qty       = await self.get_consolidated_position(asset_id)
        avg_price = await self.calculate_avg_price(asset_id)

        invested      = qty * avg_price
        current_value = (qty * current_price if current_price and qty > 0 else invested)

        return_pct = (
            (current_value - invested) / invested * 100
            if invested > 0 else Decimal("0")
        )

        return {
            "quantity":      qty,
            "avg_price":     avg_price,
            "invested":      invested,
            "current_value": current_value,
            "return_pct":    return_pct,
        }

    # -----------------------------------------------------------------
    # Listagem por janela de liquidez
    # -----------------------------------------------------------------

    async def get_by_liquidity(self, liquidity: LiquidityWindow) -> list[Asset]:
        """
        Lista ativos por janela de liquidez (D+0, D+1, D+2, vencimento).

        Fundamental para o gráfico de liquidez acumulada da carteira:
        quanto está disponível hoje, amanhã, em D+2 etc.
        Inclui as posições carregadas para que o chamador possa calcular
        os valores sem queries adicionais.
        """
        result = await self._session.execute(
            select(Asset)
            .where(Asset.liquidity == liquidity)
            .options(selectinload(Asset.positions))
            .order_by(func.coalesce(Asset.ticker, Asset.name))
        )
        return list(result.scalars().all())

    # -----------------------------------------------------------------
    # Valor total da carteira por tipo de ativo
    # -----------------------------------------------------------------

    async def get_portfolio_summary_by_type(
        self,
    ) -> dict[AssetType, dict[str, Decimal]]:
        """
        Retorna o custo investido agrupado por tipo de ativo.

        Para cada AssetType presente na carteira retorna:
            {
                "buy_cost":      custo total das compras (qty * price + fees),
                "sell_proceeds": receita total das vendas (qty * price - fees),
                "net_quantity":  posição líquida atual,
            }

        O "valor de mercado" não é calculado aqui — depende de preços
        atuais obtidos por APIs externas (yfinance, python-bcb).
        Essa responsabilidade fica na camada de serviço, que combina
        estes dados com as cotações em tempo real.
        """
        # Consulta que agrega buy_cost, sell_proceeds e net_quantity
        # por tipo de ativo, fazendo JOIN entre AssetPosition e Asset.
        result = await self._session.execute(
            select(
                Asset.asset_type,
                func.coalesce(
                    func.sum(
                        AssetPosition.quantity * AssetPosition.unit_price + AssetPosition.fees
                    ).filter(AssetPosition.operation_type == OperationType.BUY),
                    Decimal("0"),
                ).label("buy_cost"),
                func.coalesce(
                    func.sum(
                        # Em vendas, quantity é negativa; abs() retorna o valor positivo
                        func.abs(AssetPosition.quantity) * AssetPosition.unit_price
                        - AssetPosition.fees
                    ).filter(AssetPosition.operation_type == OperationType.SELL),
                    Decimal("0"),
                ).label("sell_proceeds"),
                func.coalesce(
                    func.sum(AssetPosition.quantity),
                    Decimal("0"),
                ).label("net_quantity"),
            )
            .join(Asset, AssetPosition.asset_id == Asset.id)
            .group_by(Asset.asset_type)
            .order_by(Asset.asset_type)
        )

        summary: dict[AssetType, dict[str, Decimal]] = {}
        for row in result.all():
            summary[row.asset_type] = {
                "buy_cost":      row.buy_cost,
                "sell_proceeds": row.sell_proceeds,
                "net_quantity":  row.net_quantity,
            }

        return summary
