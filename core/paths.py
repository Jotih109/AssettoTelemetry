import os
import sys

def get_base_dir() -> str:
    """
    Retorna o diretório raiz da aplicação.

    - Em modo .exe (PyInstaller congelado): retorna a pasta onde o .exe está localizado.
    - Em modo script Python: sobe um nível a partir de core/ para retornar a raiz do projeto.
    """
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    # Este arquivo fica em core/paths.py, então o pai do diretório deste arquivo é a raiz
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ensure_dir(dir_path: str) -> str:
    """Garante que o diretório exista e o retorna."""
    os.makedirs(dir_path, exist_ok=True)
    return dir_path


def get_app_dir(subfolder_name: str) -> str:
    """Retorna o caminho absoluto de uma pasta da aplicação na raiz e garante que ela exista."""
    path = os.path.join(get_base_dir(), subfolder_name)
    return ensure_dir(path)
