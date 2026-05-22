from PyQt6.QtCore import QObject, pyqtSignal


class _AppSignals(QObject):
    # Emitido sempre que dados persistentes mudam (contas, lançamentos, ativos…)
    data_changed = pyqtSignal()

    # Emitido por AccountCard quando o usuário clica "+ Lançamento" em uma conta.
    # O int é o account_id a ser pré-selecionado no diálogo de novo lançamento.
    open_new_transaction_for_account = pyqtSignal(int)


app_signals = _AppSignals()
