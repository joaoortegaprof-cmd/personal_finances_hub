"""
FinanceHub — Sistema de cores semânticas globais
================================================

Importar com:
    from frontend.components.colors import *
ou:
    from frontend.components.colors import COLOR_INCOME, COLOR_EXPENSE, ...

Semântica:
  - Passivos / dívidas / despesas  → vermelho
  - Ativos / receitas / saldo +    → verde
  - Investimentos / aportes        → azul
  - Valores neutros / patrimônio   → branco / cinza claro
  - Categorias de ativo            → cores próprias consistentes
"""

# ── Passivos, dívidas, despesas ─────────────────────────────────────────────
COLOR_PASSIVE  = "#FF4D4D"   # dívidas, passivos
COLOR_EXPENSE  = "#FF6B6B"   # despesas, saídas
COLOR_DEBT     = "#FF4D4D"   # saldo devedor
COLOR_DANGER   = "#CC3D3D"   # perigo, crítico

# ── Ativos, receitas, saldo positivo ────────────────────────────────────────
COLOR_ASSET    = "#00C896"   # ativos, positivo
COLOR_INCOME   = "#00C896"   # receitas, entradas
COLOR_POSITIVE = "#00E5AC"   # destaque positivo

# ── Investimentos, aportes ──────────────────────────────────────────────────
COLOR_INVESTMENT = "#4A9EFF"   # investimentos em geral
COLOR_APORT      = "#378ADD"   # aportes, contribuições

# ── Valores neutros / patrimônio ────────────────────────────────────────────
COLOR_NEUTRAL    = "#E8EAED"   # texto neutro primário
COLOR_PATRIMONY  = "#FFFFFF"   # patrimônio total, destaque branco
COLOR_BALANCE    = "#C8CAD8"   # saldo/label secundário

# ── Utilitários de UI ───────────────────────────────────────────────────────
COLOR_MUTED      = "#8B90A7"   # texto apagado / labels
COLOR_BORDER     = "#2E3250"   # bordas sutis
COLOR_SURFACE    = "#222640"   # fundo de cards
COLOR_SURFACE2   = "#2A2E4A"   # fundo de inputs / hover
COLOR_BG         = "#1A1D2E"   # fundo principal
COLOR_GRID       = "#2A2D3E"   # grade de gráficos

# ── Aviso / alerta ──────────────────────────────────────────────────────────
COLOR_WARNING    = "#FFB347"   # laranja — aviso
COLOR_CAUTION    = "#F39C12"   # amarelo-ouro — atenção

# ── Categorias de ativo (B3) ────────────────────────────────────────────────
COLOR_STOCK           = "#00C896"   # Ações → verde
COLOR_FII             = "#4A9EFF"   # FIIs → azul
COLOR_ETF             = "#9B59B6"   # ETFs → roxo
COLOR_TREASURY        = "#F39C12"   # Tesouro Direto → dourado
COLOR_FIXED_INCOME    = "#1ABC9C"   # Renda Fixa → verde água
COLOR_CRYPTO          = "#E67E22"   # Criptomoedas → laranja
COLOR_INTERNATIONAL   = "#3498DB"   # Ativos internacionais → azul claro
COLOR_CASH            = "#95A5A6"   # Espécie / conta → cinza
COLOR_PENSION         = "#A78BFA"   # Previdência → lilás
COLOR_OTHER           = "#8B90A7"   # Outros → cinza médio

# ── Paletas por categoria (tons do escuro ao claro para múltiplos ativos) ───
PALETTE_STOCK = [
    "#00C896", "#00A87E", "#00D4A8", "#00E5AC",
    "#7FFFD4", "#34D399", "#6EE7B7",
]
PALETTE_FII = [
    "#4A9EFF", "#2980B9", "#5DADE2", "#85C1E9",
    "#AED6F1", "#1A6EC4", "#378ADD",
]
PALETTE_ETF = [
    "#9B59B6", "#8E44AD", "#A569BD", "#BB8FCE",
    "#D2B4DE", "#7D3C98", "#C39BD3",
]
PALETTE_TREASURY = [
    "#F39C12", "#D68910", "#F0B27A", "#FAD7A0",
    "#F5CBA7", "#DC7633", "#E59866",
]
PALETTE_FIXED_INCOME = [
    "#1ABC9C", "#17A589", "#48C9B0", "#76D7C4",
    "#A2D9CE", "#0E8A7A", "#45B7AA",
]
PALETTE_CRYPTO = [
    "#E67E22", "#CA6F1E", "#EB984E", "#F0B27A",
    "#FAD7A0", "#BA4A00", "#D35400",
]

# ── Mapa asset_type → cor principal ─────────────────────────────────────────
CATEGORY_COLOR: dict[str, str] = {
    "acao":                COLOR_STOCK,
    "fii":                 COLOR_FII,
    "etf":                 COLOR_ETF,
    "tesouro_direto":      COLOR_TREASURY,
    "renda_fixa":          COLOR_FIXED_INCOME,
    "criptomoeda":         COLOR_CRYPTO,
    "acao_internacional":  COLOR_INTERNATIONAL,
    "previdencia":         COLOR_PENSION,
    "outros":              COLOR_OTHER,
}

# ── Mapa asset_type → paleta ─────────────────────────────────────────────────
CATEGORY_PALETTE: dict[str, list[str]] = {
    "acao":               PALETTE_STOCK,
    "fii":                PALETTE_FII,
    "etf":                PALETTE_ETF,
    "tesouro_direto":     PALETTE_TREASURY,
    "renda_fixa":         PALETTE_FIXED_INCOME,
    "criptomoeda":        PALETTE_CRYPTO,
    "acao_internacional": PALETTE_FII,      # reutiliza azul
    "previdencia":        PALETTE_ETF,      # reutiliza roxo
    "outros":             [COLOR_OTHER],
}

# ── RGB tuples (0.0–1.0) para matplotlib ─────────────────────────────────────
def hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    """Converte '#RRGGBB' → (r, g, b) floats 0.0–1.0 para matplotlib."""
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


COLOR_ASSET_RGB      = hex_to_rgb(COLOR_ASSET)
COLOR_EXPENSE_RGB    = hex_to_rgb(COLOR_EXPENSE)
COLOR_INVESTMENT_RGB = hex_to_rgb(COLOR_INVESTMENT)
COLOR_WARNING_RGB    = hex_to_rgb(COLOR_WARNING)
