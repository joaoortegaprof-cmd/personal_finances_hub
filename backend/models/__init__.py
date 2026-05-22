# Importa todos os models para que Base.metadata os registre antes do init_db.
from backend.models.account import Account, CreditCard, CreditCardInvoice  # noqa: F401
from backend.models.alert_history import AlertHistory  # noqa: F401
from backend.models.asset import Asset, AssetPosition  # noqa: F401
from backend.models.debt import Debt  # noqa: F401
from backend.models.dividend import Dividend  # noqa: F401
from backend.models.transaction import Transaction  # noqa: F401
