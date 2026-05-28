"""
Repositório de ativos e posições de investimento.
"""

from decimal import Decimal

from sqlalchemy import case, func, select
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
        Retorna a quantidade líquida atual do ativo considerando apenas
        operações com sinal consistente:

            BUY  com quantity > 0  → soma positiva (compra real)
            SELL com quantity < 0  → soma negativa (venda real)

        Entradas inconsistentes (SELL positivo, BUY negativo, qty=0) são
        ignoradas via CASE, tornando o cálculo robusto contra dados de
        ajustes antigos com sinal errado.
        """
        result = await self._session.execute(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (AssetPosition.operation_type == OperationType.BUY)
                                & (AssetPosition.quantity > 0),
                                AssetPosition.quantity,
                            ),
                            (
                                (AssetPosition.operation_type == OperationType.SELL)
                                & (AssetPosition.quantity < 0),
                                AssetPosition.quantity,
                            ),
                            else_=0,
                        )
                    ),
                    Decimal("0"),
                )
            ).where(AssetPosition.asset_id == asset_id)
        )
        return result.scalar() or Decimal("0")

    # -----------------------------------------------------------------
    # Preço médio ponderado (aproximação)
    # -----------------------------------------------------------------

    async def calculate_avg_price(self, asset_id: int) -> Decimal:
        """
        Preço médio ponderado pelo método CMP iterativo (Custo Médio Ponderado).

        Processa todas as operações em ordem cronológica (id como desempate):

          BUY  qty>0 price>0 → atualiza a média:
                avg = (pos_atual × avg_atual + qty × price) / (pos_atual + qty)
          SELL qty<0         → mantém a média; reduz a posição.
                              Se posição chegar a 0, reseta avg para 0.

        O reset ao zerar é fundamental para o ajuste manual funcionar:
        após um SELL que zera a posição seguido de um BUY com o novo preço
        alvo, o avg reflete exatamente o novo preço — sem contaminação do
        histórico anterior.

        Retorna 0 se não houver posição ativa.
        """
        result = await self._session.execute(
            select(AssetPosition)
            .where(AssetPosition.asset_id == asset_id)
            .order_by(AssetPosition.operation_date, AssetPosition.id)
        )
        operations = result.scalars().all()

        current_qty = Decimal("0")
        current_avg = Decimal("0")

        for op in operations:
            qty   = Decimal(str(op.quantity))
            price = Decimal(str(op.unit_price))

            if op.operation_type == OperationType.BUY and qty > 0 and price > 0:
                # CMP: recalcula a média ponderada incluindo a nova compra
                if current_qty > 0:
                    current_avg = (current_qty * current_avg + qty * price) / (current_qty + qty)
                else:
                    current_avg = price          # primeira compra (ou após zerar)
                current_qty += qty

            elif op.operation_type == OperationType.SELL and qty < 0:
                # Venda: avg não muda, apenas reduz a posição
                current_qty += qty               # qty é negativo → subtrai
                if current_qty <= 0:
                    current_qty = Decimal("0")
                    current_avg = Decimal("0")   # posição zerada — reseta a base

        return current_avg.quantize(Decimal("0.000001"))

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
    ) -> list[dict[str, Decimal]]:
        """
        Retorna o custo investido agrupado por tipo de ativo.

        Para cada AssetType presente na carteira retorna um dict com:
            asset_type    — tipo do ativo
            buy_cost      — soma de (qty × price + fees) apenas para
                            BUY com qty > 0 (compras reais)
            sell_proceeds — soma de (abs(qty) × price − fees) apenas para
                            SELL com qty < 0 (vendas reais com sinal correto)
            net_invested  — buy_cost − sell_proceeds
            net_quantity  — posição líquida (BUY+ corretos − SELL− corretos)

        Usa CASE explícito por sinal para ser robusto contra entradas
        históricas com sinal incorreto (e.g. SELL armazenado com qty > 0).
        """
        result = await self._session.execute(
            select(
                Asset.asset_type,
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (AssetPosition.operation_type == OperationType.BUY)
                                & (AssetPosition.quantity > 0),
                                AssetPosition.quantity * AssetPosition.unit_price
                                + AssetPosition.fees,
                            ),
                            else_=0,
                        )
                    ),
                    Decimal("0"),
                ).label("buy_cost"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (AssetPosition.operation_type == OperationType.SELL)
                                & (AssetPosition.quantity < 0),
                                func.abs(AssetPosition.quantity) * AssetPosition.unit_price
                                - AssetPosition.fees,
                            ),
                            else_=0,
                        )
                    ),
                    Decimal("0"),
                ).label("sell_proceeds"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (AssetPosition.operation_type == OperationType.BUY)
                                & (AssetPosition.quantity > 0),
                                AssetPosition.quantity,
                            ),
                            (
                                (AssetPosition.operation_type == OperationType.SELL)
                                & (AssetPosition.quantity < 0),
                                AssetPosition.quantity,
                            ),
                            else_=0,
                        )
                    ),
                    Decimal("0"),
                ).label("net_quantity"),
            )
            .join(Asset, AssetPosition.asset_id == Asset.id)
            .group_by(Asset.asset_type)
            .order_by(Asset.asset_type)
        )

        return [
            {
                "asset_type":    row.asset_type,
                "buy_cost":      row.buy_cost,
                "sell_proceeds": row.sell_proceeds,
                "net_invested":  row.buy_cost - row.sell_proceeds,
                "net_quantity":  row.net_quantity,
            }
            for row in result.all()
        ]
