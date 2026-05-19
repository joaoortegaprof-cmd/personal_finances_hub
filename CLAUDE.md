# FinanceHub — Contexto do Projeto

## O que é
Aplicação desktop de gestão financeira pessoal desenvolvida em Python,
com potencial de virar produto comercial no futuro.

## Stack definida
- Interface: PyQt6
- API interna: FastAPI
- Banco de dados: SQLite (dev) → PostgreSQL (produção)
- ORM: SQLAlchemy 2.0 + Alembic para migrações
- Gráficos: Plotly + PyQtWebEngine
- Dados de mercado: yfinance, python-bcb
- Testes: pytest
- Empacotamento futuro: PyInstaller

## Funcionalidades planejadas
1. Dashboard com visão geral do patrimônio e evolução histórica
2. Lançamentos manuais de receitas e despesas (ativos e passivos)
3. Controle de cartão de crédito por fatura
4. Visualização de ativos na B3 e Tesouro Direto
5. Indicadores fundamentalistas (P/L, P/VP, DY, ROE...)
6. Futuro: integração com Open Finance (Banco Central)

## Princípios de desenvolvimento
- Construir incrementalmente, uma feature por vez
- Código bem comentado (projeto de aprendizado)
- Arquitetura preparada para escalar para SaaS
- Commits pequenos e descritivos (Conventional Commits)

## Status atual
Início do projeto — estrutura de pastas ainda não criada.

## Funcionalidades avançadas — Perspectiva do investidor

### Análise de carteira
- Risco e volatilidade (Beta, desvio padrão, VaR)
- Diversificação por setor, classe de ativo, país e moeda
- Comparação com benchmarks (IBOV, CDI, IPCA, S&P500)
- Rebalanceamento: alerta quando sair da alocação alvo

### Proventos e rendimentos
- Histórico de dividendos, JCP e rendimentos de FIIs recebidos
- Custo de oportunidade (retorno real vs CDI/inflação)

### Fiscal
- Controle de IR sobre renda variável (isenção até R$20k/mês em ações)
- Alertas de emissão de DARF
- Come-cotas de fundos (maio e novembro)

### Liquidez da carteira (D+X)
- Classificação de cada ativo por janela de liquidez:
  - D+0: conta corrente, carteira digital
  - D+1: Tesouro Selic, CDB liquidez diária
  - D+2: ações e FIIs (liquidação B3)
  - Vencimento: CDB sem liquidez, Tesouro Prefixado/IPCA+
- Gráfico de liquidez acumulada: quanto está disponível em D+1, D+7, D+30
- Alerta se liquidez imediata ficar abaixo de valor mínimo configurável
- Distinção entre resgate sem perda vs resgate com deságio

### Planejamento
- Simulador de aposentadoria com projeção de aportes
- Planejamento por objetivos (imóvel, viagem, educação, carro)

---

## Funcionalidades avançadas — Gestão financeira pessoal

### Saúde financeira
- Score de saúde financeira (reserva, dívidas, taxa de poupança)
- Taxa de poupança mensal (% da renda guardada)
- Fundo de emergência: meta e progresso (referência: 6x despesas mensais)

### Controle de gastos
- Análise de padrão de gastos com detecção de anomalias e tendências
- Controle de gastos recorrentes (assinaturas, planos, contratos fixos)
- Projeção de fluxo de caixa futuro (receitas e despesas previstas)

---

## Sistema de alertas inteligentes

### Alertas fiscais
- DARF de renda variável: avisar quando vendas de ações no mês superarem R$20k
- Come-cotas: lembrete em abril/outubro (antecipa maio/novembro)
- Vencimento de títulos do Tesouro Direto
- Vencimento de CDBs e outros títulos de renda fixa

### Alertas financeiros
- Fatura do cartão de crédito próxima do vencimento (configurável: X dias antes)
- Dívidas com vencimento nos próximos X dias
- Taxa de poupança abaixo da meta configurada
- Ativo fora da faixa de alocação alvo (rebalanceamento)
- Liquidez imediata abaixo do mínimo configurado
- Taxas de juros de dívidas acima da rentabilidade dos investimentos

### Implementação dos alertas
- Alertas exibidos no dashboard e como notificação do sistema operacional
- Configuração individual: ativar/desativar e definir antecedência
- Histórico de alertas disparados

## Funcionalidades avançadas — Perspectiva do investidor

### Análise de carteira
- Risco e volatilidade (Beta, desvio padrão, VaR)
- Diversificação por setor, classe de ativo, país e moeda
- Comparação com benchmarks (IBOV, CDI, IPCA, S&P500)
- Rebalanceamento: alerta quando sair da alocação alvo

### Proventos e rendimentos
- Histórico de dividendos, JCP e rendimentos de FIIs recebidos
- Custo de oportunidade (retorno real vs CDI/inflação)

### Fiscal
- Controle de IR sobre renda variável (isenção até R$20k/mês em ações)
- Alertas de emissão de DARF
- Come-cotas de fundos (maio e novembro)

### Liquidez da carteira (D+X)
- Classificação de cada ativo por janela de liquidez:
  - D+0: conta corrente, carteira digital
  - D+1: Tesouro Selic, CDB liquidez diária
  - D+2: ações e FIIs (liquidação B3)
  - Vencimento: CDB sem liquidez, Tesouro Prefixado/IPCA+
- Gráfico de liquidez acumulada: quanto está disponível em D+1, D+7, D+30
- Alerta se liquidez imediata ficar abaixo de valor mínimo configurável
- Distinção entre resgate sem perda vs resgate com deságio

### Planejamento
- Simulador de aposentadoria com projeção de aportes
- Planejamento por objetivos (imóvel, viagem, educação, carro)

---

## Funcionalidades avançadas — Gestão financeira pessoal

### Saúde financeira
- Score de saúde financeira (reserva, dívidas, taxa de poupança)
- Taxa de poupança mensal (% da renda guardada)
- Fundo de emergência: meta e progresso (referência: 6x despesas mensais)

### Controle de gastos
- Análise de padrão de gastos com detecção de anomalias e tendências
- Controle de gastos recorrentes (assinaturas, planos, contratos fixos)
- Projeção de fluxo de caixa futuro (receitas e despesas previstas)

---

## Sistema de alertas inteligentes

### Alertas fiscais
- DARF de renda variável: avisar quando vendas de ações no mês superarem R$20k
- Come-cotas: lembrete em abril/outubro (antecipa maio/novembro)
- Vencimento de títulos do Tesouro Direto
- Vencimento de CDBs e outros títulos de renda fixa

### Alertas financeiros
- Fatura do cartão de crédito próxima do vencimento (configurável: X dias antes)
- Dívidas com vencimento nos próximos X dias
- Taxa de poupança abaixo da meta configurada
- Ativo fora da faixa de alocação alvo (rebalanceamento)
- Liquidez imediata abaixo do mínimo configurado
- Taxas de juros de dívidas acima da rentabilidade dos investimentos

### Implementação dos alertas
- Alertas exibidos no dashboard e como notificação do sistema operacional
- Configuração individual: ativar/desativar e definir antecedência
- Histórico de alertas disparados
