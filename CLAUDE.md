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
