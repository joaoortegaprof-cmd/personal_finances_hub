"""
Backup automático do banco de dados SQLite do FinanceHub.

Estratégia:
  - Cópia simples via shutil.copy2 (preserva metadados).
  - Nomes com timestamp: financehub_YYYYMMDD_HHMMSS.db
  - Rotação automática: mantém apenas os últimos N backups (padrão: 30).

Uso via CLI:
    python scripts/backup.py

Uso programático:
    from scripts.backup import create_backup
    path = create_backup()          # usa defaults
    path = create_backup(keep_last=7)   # mantém só 7 backups
"""

import shutil
from datetime import datetime
from pathlib import Path

from loguru import logger


def create_backup(
    db_path: str = "finance_hub.db",
    backup_dir: str = "data/backups",
    keep_last: int = 30,
) -> str:
    """
    Cria um backup do banco de dados e remove os mais antigos.

    Parâmetros
    ----------
    db_path:    Caminho para o banco de dados principal.
    backup_dir: Diretório onde os backups serão armazenados.
    keep_last:  Número máximo de backups a manter (remove os mais antigos).

    Retorna
    -------
    Caminho absoluto do arquivo de backup criado, ou string vazia
    se o banco não for encontrado.
    """
    db = Path(db_path)
    if not db.exists():
        logger.warning("Banco não encontrado para backup: {}", db_path)
        return ""

    backup_path = Path(backup_dir)
    backup_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = backup_path / f"financehub_{timestamp}.db"

    shutil.copy2(db, dest)
    logger.info("Backup criado: {}", dest)

    # Rotação: remove backups mais antigos além do limite
    backups = sorted(backup_path.glob("financehub_*.db"))
    if len(backups) > keep_last:
        for old in backups[:-keep_last]:
            old.unlink()
            logger.info("Backup antigo removido: {}", old)

    return str(dest)


if __name__ == "__main__":
    path = create_backup()
    if path:
        print(f"Backup criado em: {path}")
    else:
        print("Backup falhou — banco não encontrado.")
