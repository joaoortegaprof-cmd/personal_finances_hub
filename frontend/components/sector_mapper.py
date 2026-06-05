"""
Mapeamento automático de tickers B3 → setor/segmento.

Usado pelo endpoint /portfolio/by-sector para classificar ativos
sem depender do campo `sector` preenchido manualmente no cadastro.
"""

STOCK_SECTORS: dict[str, str] = {
    # Financeiro
    "BBAS3": "Financeiro", "BBDC4": "Financeiro",
    "ITUB4": "Financeiro", "SANB11": "Financeiro",
    "BPAC11": "Financeiro", "BRSR6": "Financeiro",
    "BMGB4": "Financeiro", "ABCB4": "Financeiro",

    # Energia e Petróleo
    "PETR4": "Energia", "PETR3": "Energia",
    "PRIO3": "Energia", "RECV3": "Energia",
    "RRRP3": "Energia", "VBBR3": "Energia",
    "CSAN3": "Energia", "UGPA3": "Energia",

    # Mineração e Siderurgia
    "VALE3": "Mineração", "CSNA3": "Siderurgia",
    "GGBR4": "Siderurgia", "USIM5": "Siderurgia",
    "BRAP4": "Mineração", "CMIN3": "Mineração",

    # Utilities (Energia Elétrica e Saneamento)
    "ELET3": "Energia Elétrica", "ELET6": "Energia Elétrica",
    "CMIG4": "Energia Elétrica", "EGIE3": "Energia Elétrica",
    "EQTL3": "Energia Elétrica", "TAEE11": "Energia Elétrica",
    "AURE3": "Energia Elétrica", "CPLE6": "Energia Elétrica",
    "SBSP3": "Saneamento", "SAPR11": "Saneamento",

    # Telecomunicações
    "VIVT3": "Telecom", "TIMS3": "Telecom",
    "OIBR3": "Telecom",

    # Consumo Básico
    "ABEV3": "Consumo Básico", "MDIA3": "Consumo Básico",
    "BRFS3": "Consumo Básico", "JBSS3": "Consumo Básico",
    "MRFG3": "Consumo Básico", "BEEF3": "Consumo Básico",
    "SLCE3": "Agronegócio", "SMTO3": "Agronegócio",
    "RAIZ4": "Agronegócio",

    # Consumo Discricionário
    "MGLU3": "Varejo", "LREN3": "Varejo",
    "AZZA3": "Varejo", "SBFG3": "Varejo",
    "PETZ3": "Varejo", "CEAB3": "Varejo",
    "BHIA3": "Varejo",

    # Saúde
    "RDOR3": "Saúde", "HAPV3": "Saúde",
    "FLRY3": "Saúde", "DASA3": "Saúde",
    "QUAL3": "Saúde", "HYPE3": "Saúde",
    "RADL3": "Saúde",

    # Tecnologia
    "TOTS3": "Tecnologia", "POSI3": "Tecnologia",
    "LWSA3": "Tecnologia", "IFCM3": "Tecnologia",

    # Imóveis e Construção
    "CYRE3": "Construção", "MRVE3": "Construção",
    "EZTC3": "Construção", "MULT3": "Shopping",
    "IGTI11": "Shopping",

    # Transporte e Logística
    "CCRO3": "Infraestrutura", "RAIL3": "Logística",
    "EMBR3": "Aeronáutica", "AZUL4": "Aviação",
    "GOLL4": "Aviação",

    # Papel e Celulose
    "SUZB3": "Papel e Celulose", "KLBN11": "Papel e Celulose",
    "DXCO3": "Papel e Celulose",

    # Educação
    "YDUQ3": "Educação", "COGN3": "Educação",
    "ANIM3": "Educação",

    # Seguros
    "BBSE3": "Seguros", "IRBR3": "Seguros",
    "PSSA3": "Seguros",
}

FII_SECTORS: dict[str, str] = {
    # Logística
    "HGLG11": "Logística", "BTLG11": "Logística",
    "XPLG11": "Logística", "TRXF11": "Logística",
    "BRCO11": "Logística", "GLOG11": "Logística",
    "ALZR11": "Logística",

    # Lajes Corporativas
    "HGRE11": "Lajes Corp.", "BRCR11": "Lajes Corp.",
    "PVBI11": "Lajes Corp.", "VINO11": "Lajes Corp.",
    "JSRE11": "Lajes Corp.", "RBRP11": "Lajes Corp.",

    # Shopping
    "VISC11": "Shopping", "XPML11": "Shopping",
    "HSML11": "Shopping", "MALL11": "Shopping",
    "HGBS11": "Shopping",

    # Recebíveis (CRI/CRA)
    "HGCR11": "Recebíveis", "KNCR11": "Recebíveis",
    "KNSC11": "Recebíveis", "RECR11": "Recebíveis",
    "MCCI11": "Recebíveis", "CPTS11": "Recebíveis",
    "DEVA11": "Recebíveis", "RBRR11": "Recebíveis",
    "IRDM11": "Recebíveis", "VGHF11": "Recebíveis",

    # Residencial
    "HGRU11": "Residencial", "RBVA11": "Residencial",
    "VILG11": "Residencial",

    # Híbrido / FOF
    "HFOF11": "Fundo de Fundos", "BCFF11": "Fundo de Fundos",
    "RBRF11": "Fundo de Fundos", "KFOF11": "Fundo de Fundos",

    # Hospital / Saúde
    "HCTR11": "Saúde", "NSLU11": "Saúde",

    # Agro
    "RURA11": "Agronegócio", "RZAG11": "Agronegócio",
}


def get_sector(ticker: str, asset_type: str) -> str:
    """Retorna o setor/segmento do ativo a partir do ticker e tipo."""
    if not ticker:
        return "Não classificado"
    t = ticker.upper().replace(".SA", "")
    if asset_type == "fii":
        return FII_SECTORS.get(t, "Outros FIIs")
    return STOCK_SECTORS.get(t, "Outros")
