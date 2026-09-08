"""
core/config.py — Preferências do usuário (config.json)
=======================================================

O que o app lembra de uma sessão para a outra: qual referência estava
selecionada, se a voz estava ligada, em que modo o engenheiro estava, quanta
volta guardar em disco. Fica num `config.json` na raiz da aplicação — do lado
do `.exe`, quando empacotado.

Regras de projeto:

* **Preferência ruim nunca derruba o app.** Arquivo corrompido, chave que não
  existe mais, tipo errado: cai no padrão e segue. É preferência, não dado.
* **Só grava quando muda de verdade.** `set()` compara antes de escrever, para
  não bater no disco a cada quadro de telemetria.
* **Gravação atômica**, igual ao resto do app: fechar o jogo no meio de uma
  escrita não pode deixar o arquivo pela metade.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from core.paths import get_base_dir

CONFIG_FILENAME = "config.json"

#: Todo padrão do app mora aqui. Uma chave ausente no arquivo do usuário cai
#: neste valor; uma chave desconhecida no arquivo é preservada, mas ignorada.
DEFAULTS: Dict[str, Any] = {
    # --- Referência (volta fantasma) ---
    # kind: "auto" | "none" | "pb" | "session" | "ideal" | "lap"
    # Com "lap", `reference_lap_id` diz qual volta do catálogo usar.
    "reference_kind": "auto",
    "reference_lap_id": "",

    # --- Engenheiro de pista ---
    "voice_enabled": True,
    "engineer_mode": "lap",          # "lap" | "live" | "manual"

    # --- Exportação ---
    "auto_export_on_best_lap": True,

    # --- Provider ---
    # Sem o jogo aberto, `mock_mode` liga o simulador interno. Também dá para
    # forçar pela linha de comando (--mock) ou por variável de ambiente
    # (APEXVIEW_MOCK=1), que ganham deste valor.
    "mock_mode": False,

    # --- Gráficos ---
    # A engine emite a 60 Hz; as curvas são redesenhadas 1 a cada N quadros.
    "graph_redraw_every_n_frames": 5,

    # --- Retenção de voltas em disco ---
    # Os números têm que bater com core.lap_library.RetentionPolicy: como estes
    # padrões SOBRESCREVEM os da dataclass, deixá-los para trás fazia o app
    # apagar volta que a documentação prometia guardar.
    # 200 voltas cobrem vários fins de semana completos (um fim de semana dá 40
    # a 60 voltas) e custam poucos MB por combinação pista/carro.
    "retention": {
        "enabled": True,
        "keep_best": 30,
        "keep_recent": 200,
    },
}

#: Valores aceitos em `reference_kind` — qualquer outra coisa cai em "auto".
REFERENCE_KINDS = ("auto", "none", "pb", "session", "ideal", "lap")

#: Valores aceitos em `engineer_mode`.
ENGINEER_MODES = ("lap", "live", "manual")


class AppConfig:
    """
    Preferências em memória, espelhadas num JSON.

    Use como um dicionário tolerante::

        cfg = AppConfig()
        cfg.get("voice_enabled")          # -> True
        cfg.set("voice_enabled", False)   # grava se mudou
    """

    def __init__(self, path: str = None):
        self.path = path or os.path.join(get_base_dir(), CONFIG_FILENAME)
        self._data: Dict[str, Any] = {}
        self.load()

    # -- disco ---------------------------------------------------------------

    def load(self) -> None:
        """Lê o arquivo. Ausente ou ilegível: fica só com os padrões."""
        self._data = {}
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self._data = loaded
            else:
                print(f"[Config] {CONFIG_FILENAME} não é um objeto JSON: usando padrões.")
        except (OSError, ValueError, UnicodeDecodeError) as e:
            print(f"[Config] {CONFIG_FILENAME} ilegível ({e}): usando padrões.")

    def save(self) -> bool:
        """Grava de forma atômica. Falha aqui é avisada, não fatal."""
        tmp_path = f"{self.path}.tmp"
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
            return True
        except OSError as e:
            print(f"[Config] Falha ao salvar {CONFIG_FILENAME}: {e}")
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            return False

    # -- acesso --------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """
        Valor da preferência, com o tipo do padrão garantido.

        Um `config.json` editado à mão pode trazer texto onde devia haver
        número. Em vez de estourar num `int()` no meio de um quadro de
        telemetria, o valor incoerente é descartado aqui.
        """
        fallback = DEFAULTS.get(key, default)
        if key not in self._data:
            return fallback
        value = self._data[key]
        if fallback is not None and not isinstance(value, type(fallback)):
            # bool é subclasse de int: 1/0 num campo booleano é aceitável
            if isinstance(fallback, bool) and isinstance(value, int):
                return bool(value)
            if isinstance(fallback, (int, float)) and isinstance(value, (int, float)):
                return type(fallback)(value)
            print(f"[Config] '{key}' com tipo inesperado "
                  f"({type(value).__name__}): usando o padrão.")
            return fallback
        return value

    def set(self, key: str, value: Any, *, save: bool = True) -> bool:
        """Guarda a preferência. Devolve True se algo mudou (e foi gravado)."""
        if self._data.get(key, DEFAULTS.get(key)) == value and key in self._data:
            return False
        self._data[key] = value
        if save:
            self.save()
        return True

    def update(self, values: Dict[str, Any], *, save: bool = True) -> bool:
        """Guarda várias preferências de uma vez, com uma gravação só."""
        changed = False
        for key, value in values.items():
            changed |= self.set(key, value, save=False)
        if changed and save:
            self.save()
        return changed

    # -- atalhos de domínio --------------------------------------------------

    def reference(self) -> tuple:
        """
        `(kind, lap_id)` da referência salva, já validado.

        `kind` fora da lista conhecida — arquivo de uma versão futura, ou
        editado à mão — cai em "auto", que é o comportamento mais útil.
        """
        kind = self.get("reference_kind")
        if kind not in REFERENCE_KINDS:
            kind = "auto"
        lap_id = self.get("reference_lap_id") or ""
        if kind == "lap" and not lap_id:
            kind = "auto"
        return kind, lap_id

    def set_reference(self, kind: str, lap_id: str = "") -> bool:
        if kind not in REFERENCE_KINDS:
            kind = "auto"
        return self.update({
            "reference_kind": kind,
            "reference_lap_id": lap_id if kind == "lap" else "",
        })

    def engineer_mode(self) -> str:
        mode = self.get("engineer_mode")
        return mode if mode in ENGINEER_MODES else "lap"

    def retention_policy(self):
        """RetentionPolicy montada a partir das preferências."""
        from core.lap_library import RetentionPolicy
        return RetentionPolicy.from_dict(self.get("retention"))


#: Instância compartilhada. O app inteiro lê e escreve a mesma configuração;
#: criar uma por janela faria uma sobrescrever a preferência da outra.
_shared: AppConfig = None


def get_config() -> AppConfig:
    global _shared
    if _shared is None:
        _shared = AppConfig()
    return _shared
