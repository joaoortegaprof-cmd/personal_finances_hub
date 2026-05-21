"""
Schemas Pydantic para ativos de investimento e posições de carteira.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from backend.models.asset import AssetIndexer, AssetType, LiquidityWindow, OperationType


class AssetCreate(BaseModel):
    ticker: str | None = Field(default=None, max_length=20)
    name: str = Field(max_length=200)
    asset_type: AssetType
    sector: str | None = Field(default=None, max_length=100)
    sub_sector: str | None = Field(default=None, max_length=100)
    currency: str = Field(default="BRL", min_length=3, max_length=3)
    indexer: AssetIndexer | None = None
    maturity_date: date | None = None
    liquidity: LiquidityWindow = LiquidityWindow.D2
    notes: str | None = None
    is_emergency_fund: bool = False


class AssetUpdate(BaseModel):
    ticker: str | None = Field(default=None, max_length=20)
    name: str | None = Field(default=None, max_length=200)
    asset_type: AssetType | None = None
    sector: str | None = Field(default=None, max_length=100)
    sub_sector: str | None = Field(default=None, max_length=100)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    indexer: AssetIndexer | None = None
    maturity_date: date | None = None
    liquidity: LiquidityWindow | None = None
    notes: str | None = None
    is_emergency_fund: bool | None = None


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker: str | None
    name: str
    asset_type: AssetType
    sector: str | None
    sub_sector: str | None
    currency: str
    indexer: AssetIndexer | None
    maturity_date: date | None
    liquidity: LiquidityWindow
    notes: str | None
    is_emergency_fund: bool
    created_at: datetime
    updated_at: datetime


class AssetOperationCreate(BaseModel):
    operation_date: date
    quantity: Decimal = Field(ne=0)
    unit_price: Decimal = Field(ge=0)
    fees: Decimal = Field(default=Decimal("0.00"), ge=0)
    operation_type: OperationType
    account_id: int | None = None
    notes: str | None = None


class AssetOperationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    asset_id: int
    operation_date: date
    quantity: Decimal
    unit_price: Decimal
    fees: Decimal
    operation_type: OperationType
    account_id: int | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class ConsolidatedPositionOut(BaseModel):
    asset_id: int
    ticker: str | None
    name: str
    asset_type: AssetType
    net_quantity: Decimal
    avg_price: Decimal
    estimated_cost: Decimal  # net_quantity × avg_price


class PortfolioTypeEntryOut(BaseModel):
    asset_type: AssetType
    buy_cost: Decimal
    sell_proceeds: Decimal
    net_invested: Decimal
    net_quantity: Decimal


class PortfolioSummaryOut(BaseModel):
    by_type: list[PortfolioTypeEntryOut]
    total_invested: Decimal


class AssetLiquidityItemOut(BaseModel):
    asset_id: int
    ticker: str | None
    name: str
    liquidity_window: LiquidityWindow
    net_quantity: Decimal
    avg_cost: Decimal
    estimated_value: Decimal
    has_loss_on_early_redemption: bool


class LiquidityBreakdownOut(BaseModel):
    d0_value: Decimal
    d1_value: Decimal
    d2_value: Decimal
    maturity_value: Decimal
    total_liquid: Decimal
    total_portfolio: Decimal
    items: list[AssetLiquidityItemOut]
