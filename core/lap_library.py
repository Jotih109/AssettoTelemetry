"""
core/lap_library.py — Biblioteca de voltas gravadas
====================================================

Substitui a gravação "um arquivo JSON gordo por volta, tudo carregado na
memória ao entrar na pista" por três ideias simples:

1. **Índice leve** (`index.json`, um por Pista/Carro). Guarda só o que a
   interface precisa para MONTAR UMA LISTA: tempo, setores, data, validade.
   Alguns KB, mesmo com centenas de voltas. É o único arquivo lido quando
   você entra na pista.

2. **Telemetria sob demanda**. Os milhares de pontos de cada volta ficam num
   arquivo separado, comprimido, e só são lidos quando aquela volta vira
   referência ou é aberta nos gráficos. Um cache pequeno evita reler a mesma.

3. **Retenção**. A pasta não cresce para sempre: as voltas mais lentas e
   antigas saem sozinhas, e as melhores (mais as fixadas com alfinete e as
   salvas à mão) ficam.

Formato em disco::

    telemetry_data/
      <Pista>/
        <Carro>/
          index.json                       <- catálogo leve
          laps/
            20260908-143512_L007.json.gz   <- telemetria, comprimida
          ideal_lap_ghost.json.gz          <- volta ideal (sintética, fora do
                                              catálogo: ninguém a deu)

Sobre tamanho: gravar `json.dump()` cru de uma volta de 90 s a 60 Hz dá
1,5 MB. Arredondando cada canal para a precisão que ele realmente tem e
comprimindo, a mesma volta ocupa ~250 KB — 6x menos, sem perda visível
em gráfico nenhum.

Compatibilidade: voltas gravadas pelas versões antigas (JSON solto na pasta
do carro) são indexadas na primeira leitura e continuam funcionando de onde
estão. Nada é reescrito nem apagado por conta própria.
"""

from __future__ import annotations

import gzip
import json
import os
import re
from collections import OrderedDict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, List, Optional

from core.paths import get_app_dir

#: Versão do formato do índice. Índice de versão diferente é reconstruído.
SCHEMA_VERSION = 2

#: Casas decimais por canal. A telemetria vem do jogo com precisão de float
#: de 32 bits, mas ninguém precisa do milímetro na coordenada nem do
#: nanossegundo no cronômetro — e cada casa a mais é um byte a mais por ponto,
#: vezes 5.000 pontos, vezes 15 canais.
CHANNEL_PRECISION: Dict[str, int] = {
    "times": 3,             # ms
    "distance": 2,          # cm
    "speed": 2,             # km/h
    "gas": 4,
    "brake": 4,
    "clutch": 4,
    "steer": 2,             # centésimo de grau
    "delta": 3,
    "car_x": 2,
    "car_y": 2,
    "car_z": 2,
    "abs_intervention": 4,
    "tc_intervention": 4,
    "g_lat": 3,
    "g_lon": 3,
}

#: Canais que são inteiros de verdade — gravar "6000.0" no lugar de "6000"
#: é desperdício puro.
CHANNEL_INTEGER = {"sector", "rpm", "gear"}


# ---------------------------------------------------------------------------
# Retenção
# ---------------------------------------------------------------------------

@dataclass
class RetentionPolicy:
    """
    Quantas voltas manter por Pista/Carro.

    Uma volta sobrevive se estiver em QUALQUER uma destas condições:
      * fixada com alfinete (`pinned`) ou salva à mão (`manual_save`)
      * entre as `keep_best` mais rápidas válidas e inteiras
      * entre as `keep_recent` mais recentes

    `keep_best` existe para você não perder a volta boa de três meses atrás,
    e `keep_recent` para a sessão de hoje ficar inteira mesmo se você estiver
    lento. Com `enabled=False` nada é apagado.

    Os padrões são deliberadamente folgados: 200 voltas cobrem vários fins de
    semana completos (um fim de semana de corrida dá 40 a 60 voltas) e custam
    uns 18 MB por combinação pista/carro. Poder revisitar a sessão de sábado
    com calma vale muito mais que esse espaço.
    """
    enabled: bool = True
    keep_best: int = 30
    keep_recent: int = 200

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "RetentionPolicy":
        if not isinstance(data, dict):
            return cls()
        base = cls()
        return cls(
            enabled=bool(data.get("enabled", base.enabled)),
            keep_best=max(0, int(data.get("keep_best", base.keep_best))),
            keep_recent=max(0, int(data.get("keep_recent", base.keep_recent))),
        )


# ---------------------------------------------------------------------------
# Registro de uma volta no índice
# ---------------------------------------------------------------------------

@dataclass
class LapRecord:
    """
    Uma linha do índice: tudo o que dá para dizer sobre uma volta SEM abrir a
    telemetria dela.
    """
    lap_id: str = ""
    file: str = ""                  # caminho relativo à pasta Pista/Carro
    track: str = ""
    car: str = ""
    session_id: str = ""
    session_type: str = ""          # Practice / Qualify / Race / Hotlap
    lap_number: int = 0
    lap_time_str: str = ""
    lap_time_ms: int = 0
    sector_times_ms: List[int] = field(default_factory=lambda: [0, 0, 0])
    timestamp: str = ""
    points: int = 0
    channels: List[str] = field(default_factory=list)
    #: Telemetria vai da linha de chegada até a linha de chegada
    full_lap: bool = False
    #: Volta limpa (não cortou a pista, sem penalidade pendente)
    valid: bool = True
    #: A volta passou pelo pit lane — é volta de saída ou de retorno aos boxes.
    #: Ela É gravada e listada (o piloto pode querer ver o que fez ali), mas
    #: não serve de referência nem de material para o coach: uma volta de
    #: retorno é 20 segundos mais lenta, e comparar contra ela diria que o
    #: piloto perdeu dois segundos em TODAS as curvas.
    pit_lap: bool = False
    #: Gravada por um clique do piloto, não pelo fim de volta automático
    manual_save: bool = False
    #: Protegida da limpeza automática
    pinned: bool = False
    #: Volta importada externamente (pacote .apex)
    imported: bool = False
    #: Arquivo herdado de versão anterior (JSON solto, sem compressão)
    legacy: bool = False

    # -- conveniências para a interface -------------------------------------

    @property
    def is_reference_material(self) -> bool:
        """
        Serve como volta de referência?

        Precisa de volta inteira, tempo legível e não ter passado pelo box.
        """
        return (self.full_lap and self.lap_time_ms > 0 and self.points > 0
                and not self.pit_lap)

    @property
    def day_key(self) -> str:
        """Chave de data em formato ISO YYYY-MM-DD para agrupamento e ordenação."""
        if not self.timestamp:
            return "Sem Data"
        try:
            return self.timestamp[:10]
        except Exception:
            return "Sem Data"

    @property
    def day_display(self) -> str:
        """Texto amigável do dia (Hoje, Ontem, ou DD/MM/AAAA - Dia da Semana)."""
        if not self.timestamp:
            return "Data Desconhecida"
        try:
            clean_ts = self.timestamp.replace("Z", "").replace(" ", "T")
            dt = datetime.fromisoformat(clean_ts)
            today = datetime.now().date()
            d = dt.date()
            d_str = dt.strftime("%d/%m/%Y")
            dias_semana = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
            dia_sem = dias_semana[dt.weekday()]
            diff = (today - d).days
            if diff == 0:
                return f"Hoje · {d_str} ({dia_sem})"
            elif diff == 1:
                return f"Ontem · {d_str} ({dia_sem})"
            else:
                return f"{d_str} · {dia_sem}"
        except Exception:
            return self.timestamp[:10] if len(self.timestamp) >= 10 else "Sem Data"

    @property
    def session_display_name(self) -> str:
        """Nome de exibição da sessão com horário e tipo (ex: 'Sessão 14:35 · Practice')."""
        hora = ""
        try:
            clean_ts = self.timestamp.replace("Z", "").replace(" ", "T")
            dt = datetime.fromisoformat(clean_ts)
            hora = dt.strftime("%H:%M")
        except Exception:
            pass

        tipo = (self.session_type or "").strip()
        if tipo and hora:
            return f"Sessão {hora} · {tipo}"
        elif tipo:
            return f"Sessão · {tipo}"
        elif hora:
            return f"Sessão {hora}"
        elif self.session_id:
            return f"Sessão {self.session_id}"
        return "Sessão Única"

    @property
    def date_str(self) -> str:
        """Data legível (dd/mm HH:MM) a partir do timestamp ISO."""
        try:
            clean_ts = self.timestamp.replace("Z", "").replace(" ", "T")
            return datetime.fromisoformat(clean_ts).strftime("%d/%m %H:%M")
        except (ValueError, TypeError):
            return ""

    def label(self, *, with_date: bool = True) -> str:
        """Texto curto para o combo de referência e listas."""
        parts = [f"Volta {self.lap_number}" if self.lap_number else "Volta",
                 self.lap_time_str or "--:--.---"]
        text = " — ".join(parts)
        if self.pit_lap:
            # Texto, não emoji: este rótulo também é impresso em console
            # (testes, log) e o Windows costuma vir em cp1252.
            text += " [box]"
        if not self.valid:
            text += " ⚠"
        if self.pinned:
            text = "📌 " + text
        if getattr(self, "imported", False):
            text += " [importada]"
        if with_date and self.date_str:
            text += f"  ({self.date_str})"
        return text

    @classmethod
    def from_dict(cls, data: dict) -> "LapRecord":
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in known}
        rec = cls(**clean)
        # Índices escritos à mão podem trazer setores curtos ou compridos
        st = list(rec.sector_times_ms or [])[:3]
        rec.sector_times_ms = st + [0] * (3 - len(st))
        return rec

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Utilidades de arquivo
# ---------------------------------------------------------------------------

_INVALID_PATH_CHARS = '<>:"/\\|?*'


def clean_name(name: str, fallback: str) -> str:
    """Nome de pista/carro utilizável como nome de pasta."""
    for c in _INVALID_PATH_CHARS:
        name = name.replace(c, '')
    name = name.strip()
    return name if name else fallback


def write_bytes_atomic(path: str, payload: bytes) -> bool:
    """Grava e só então substitui o definitivo — arquivo truncado nunca."""
    tmp_path = f"{path}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp_path, 'wb') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        return True
    except OSError as e:
        print(f"[LapLibrary] Falha ao gravar {os.path.basename(path)}: {e}")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        return False


def read_json_file(path: str):
    """
    Lê um JSON, comprimido ou não, tolerando arquivo corrompido.

    Arquivo ilegível é renomeado para *.corrupt: não adianta tentar de novo a
    cada volta, e jogar fora sem deixar rastro atrapalha quem for investigar.
    """
    try:
        is_gz = path.endswith(".gz") or path.endswith(".apex")
        if not is_gz and os.path.exists(path):
            try:
                with open(path, 'rb') as test_f:
                    if test_f.read(2) == b'\x1f\x8b':
                        is_gz = True
            except OSError:
                pass
        opener = gzip.open if is_gz else open
        with opener(path, 'rt', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError, UnicodeDecodeError, EOFError) as e:
        print(f"[LapLibrary] Arquivo inválido, ignorando: {path} ({e})")
        try:
            os.replace(path, f"{path}.corrupt")
        except OSError:
            pass
        return None


def write_json_gzip_atomic(path: str, data: dict) -> bool:
    """Grava um JSON comprimido, de forma atômica."""
    blob = gzip.compress(
        json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode("utf-8"),
        compresslevel=6)
    return write_bytes_atomic(path, blob)


def compact_telemetry(telemetry: dict) -> dict:
    """
    Arredonda cada canal para a precisão que ele realmente carrega.

    Canal desconhecido passa sem alteração — um provider novo que traga um
    canal a mais não pode ter os dados destruídos aqui.
    """
    out = {}
    for key, values in (telemetry or {}).items():
        if not isinstance(values, list):
            out[key] = values
            continue
        if key in CHANNEL_INTEGER:
            out[key] = [int(v) for v in values]
        elif key in CHANNEL_PRECISION:
            nd = CHANNEL_PRECISION[key]
            out[key] = [round(float(v), nd) for v in values]
        else:
            out[key] = values
    return out


# ---------------------------------------------------------------------------
# Biblioteca
# ---------------------------------------------------------------------------

_LEGACY_SKIP = {"best_lap_ghost.json", "ideal_lap_ghost.json", "index.json"}
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]")


class LapLibrary:
    """
    Catálogo de voltas gravadas, organizado por Pista/Carro.

    Uma instância serve o app inteiro. O índice de cada combinação é lido do
    disco uma vez e mantido em memória; a telemetria só é lida quando alguém
    pede uma volta específica.
    """

    #: Quantas telemetrias completas manter em memória ao mesmo tempo. Cada
    #: uma custa alguns MB, então o cache é deliberadamente pequeno: o uso
    #: real é "a referência + a volta aberta nos gráficos".
    CACHE_SIZE = 4

    def __init__(self, data_dir: Optional[str] = None,
                 retention: Optional[RetentionPolicy] = None):
        self.data_dir = data_dir if data_dir else get_app_dir("telemetry_data")
        self.retention = retention or RetentionPolicy()
        self._index_cache: Dict[str, List[LapRecord]] = {}
        self._telemetry_cache: "OrderedDict[str, dict]" = OrderedDict()

    # -- caminhos ------------------------------------------------------------

    def folder_for(self, track: str, car: str) -> str:
        return os.path.join(self.data_dir,
                            clean_name(track, "UnknownTrack"),
                            clean_name(car, "UnknownCar"))

    def _index_path(self, track: str, car: str) -> str:
        return os.path.join(self.folder_for(track, car), "index.json")

    @staticmethod
    def _key(track: str, car: str) -> str:
        return f"{clean_name(track, 'UnknownTrack')}|{clean_name(car, 'UnknownCar')}"

    # -- índice --------------------------------------------------------------

    def records(self, track: str, car: str, *,
                only_valid: bool = False,
                only_full: bool = False,
                reload: bool = False) -> List[LapRecord]:
        """
        Índice da combinação, da volta mais recente para a mais antiga.

        Devolve a lista viva mantida em memória quando não há filtro, para que
        quem grava uma volta não precise reler o disco.
        """
        key = self._key(track, car)
        if reload or key not in self._index_cache:
            self._index_cache[key] = self._load_index(track, car)
        recs = self._index_cache[key]
        if only_valid:
            recs = [r for r in recs if r.valid]
        if only_full:
            recs = [r for r in recs if r.is_reference_material]
        return recs

    def find(self, track: str, car: str, lap_id: str) -> Optional[LapRecord]:
        for rec in self.records(track, car):
            if rec.lap_id == lap_id:
                return rec
        return None

    def _load_index(self, track: str, car: str) -> List[LapRecord]:
        path = self._index_path(track, car)
        recs: List[LapRecord] = []
        if os.path.exists(path):
            data = read_json_file(path)
            if isinstance(data, dict) and data.get("schema") == SCHEMA_VERSION:
                for raw in data.get("laps", []):
                    if isinstance(raw, dict):
                        recs.append(LapRecord.from_dict(raw))
            elif data is not None:
                print("[LapLibrary] Índice de versão antiga: será reconstruído.")

        recs = self._drop_missing(track, car, recs)
        recs.extend(self._scan_unindexed(track, car, recs))
        recs.sort(key=lambda r: (r.timestamp, r.lap_number), reverse=True)
        return recs

    def _drop_missing(self, track: str, car: str,
                      recs: List[LapRecord]) -> List[LapRecord]:
        """Tira do índice as voltas cujo arquivo sumiu (apagado à mão)."""
        folder = self.folder_for(track, car)
        alive = [r for r in recs if r.file and
                 os.path.exists(os.path.join(folder, r.file))]
        if len(alive) != len(recs):
            print(f"[LapLibrary] {len(recs) - len(alive)} volta(s) do índice "
                  "não existem mais em disco: removidas do catálogo.")
        return alive

    def _scan_unindexed(self, track: str, car: str,
                        known: List[LapRecord]) -> List[LapRecord]:
        """
        Cataloga voltas que estão em disco mas fora do índice. Dois casos:

        * `laps/*.json.gz` — voltas do formato atual cujo índice se perdeu
          (corrompido, apagado). Sem esta varredura, um `index.json` estragado
          transformaria todas as voltas gravadas em arquivos órfãos.
        * `*.json` solto na pasta do carro — voltas das versões anteriores.

        Acontece uma vez: a varredura já grava o índice reconstruído. É o único
        momento em que a telemetria de várias voltas é aberta de uma vez, e é
        por isso que existe um índice.
        """
        folder = self.folder_for(track, car)
        if not os.path.isdir(folder):
            return []
        seen = {r.file for r in known}
        found: List[LapRecord] = []

        def absorb(rel_path: str, legacy: bool) -> None:
            if rel_path in seen:
                return
            data = read_json_file(os.path.join(folder, rel_path))
            if not isinstance(data, dict) or "telemetry" not in data:
                return
            stem = os.path.basename(rel_path)
            for ext in (".json.gz", ".json"):
                if stem.endswith(ext):
                    stem = stem[:-len(ext)]
                    break
            rec = self._record_from_payload(
                data, file=rel_path, track=track, car=car,
                lap_id=_SAFE_ID.sub("_", stem))
            rec.legacy = legacy
            found.append(rec)

        laps_dir = os.path.join(folder, "laps")
        if os.path.isdir(laps_dir):
            for fname in sorted(os.listdir(laps_dir)):
                if fname.endswith(".json.gz") or fname.endswith(".json"):
                    absorb(f"laps/{fname}", legacy=False)

        for fname in sorted(os.listdir(folder)):
            if fname.endswith(".json") and fname not in _LEGACY_SKIP:
                absorb(fname, legacy=True)

        if found:
            recovered = sum(1 for r in found if not r.legacy)
            if recovered:
                print(f"[LapLibrary] {recovered} volta(s) recuperada(s) do disco "
                      "(índice ausente ou corrompido).")
            if len(found) - recovered:
                print(f"[LapLibrary] {len(found) - recovered} volta(s) de versão "
                      "anterior adicionadas ao catálogo.")
            self._index_cache[self._key(track, car)] = known + found
            self._write_index(track, car, known + found)
        return found

    def _record_from_payload(self, payload: dict, *, file: str, track: str,
                             car: str, lap_id: str) -> LapRecord:
        meta = payload.get("metadata", {}) or {}
        telemetry = payload.get("telemetry", {}) or {}
        times = telemetry.get("times") or []
        from core.session_manager import parse_lap_time_ms  # import tardio: ciclo
        lap_time_str = meta.get("lap_time_str", "") or ""
        return LapRecord(
            lap_id=lap_id,
            file=file,
            track=meta.get("track", track) or track,
            car=meta.get("car", car) or car,
            session_id=meta.get("session_id", "") or "",
            session_type=meta.get("session_type", "") or "",
            lap_number=int(meta.get("lap_number", 0) or 0),
            lap_time_str=lap_time_str,
            lap_time_ms=parse_lap_time_ms(lap_time_str),
            sector_times_ms=list(meta.get("sector_times_ms", [0, 0, 0]))[:3] or [0, 0, 0],
            timestamp=meta.get("timestamp", "") or "",
            points=len(times),
            channels=sorted(k for k, v in telemetry.items() if isinstance(v, list) and v),
            full_lap=bool(meta.get("full_lap", False)),
            valid=bool(meta.get("valid", True)),
            pit_lap=bool(meta.get("pit_lap", False)),
            manual_save=bool(meta.get("manual_save", False)),
            pinned=bool(meta.get("pinned", False)),
            imported=bool(meta.get("imported", False)),
        )

    def _write_index(self, track: str, car: str, recs: List[LapRecord]) -> bool:
        payload = {
            "schema": SCHEMA_VERSION,
            "track": clean_name(track, "UnknownTrack"),
            "car": clean_name(car, "UnknownCar"),
            "updated": datetime.now().isoformat(timespec="seconds"),
            "laps": [r.to_dict() for r in recs],
        }
        blob = json.dumps(payload, ensure_ascii=False, indent=1).encode("utf-8")
        return write_bytes_atomic(self._index_path(track, car), blob)

    # -- gravação ------------------------------------------------------------

    def _unique_lap_id(self, track: str, car: str, base_id: str) -> tuple:
        """
        `(lap_id, caminho_relativo)` livres, a partir de um id candidato.

        O id sai de um carimbo de data com precisão de segundo. Duas gravações
        no MESMO segundo — a importação do Personal Best antigo junto com a
        primeira volta, ou uma volta de reconhecimento curtíssima — geravam o
        mesmo id: a segunda sobrescrevia o arquivo da primeira e o índice
        ficava com duas linhas apontando para a mesma volta.
        """
        folder = self.folder_for(track, car)
        taken = {r.lap_id for r in self.records(track, car)}
        lap_id = base_id
        n = 1
        while lap_id in taken or os.path.exists(
                os.path.join(folder, "laps", f"{lap_id}.json.gz")):
            lap_id = f"{base_id}-{n}"
            n += 1
        return lap_id, f"laps/{lap_id}.json.gz"

    def save_lap(self, track: str, car: str, *, telemetry: dict,
                 lap_time_str: str, sector_times_ms: List[int],
                 lap_number: int = 0, session_id: str = "",
                 session_type: str = "",
                 full_lap: bool = False, valid: bool = True,
                 pit_lap: bool = False, manual: bool = False,
                 extra_metadata: Optional[dict] = None) -> Optional[LapRecord]:
        """
        Grava uma volta e devolve o registro dela no índice.

        A telemetria vai comprimida e arredondada para um arquivo próprio; só
        os metadados entram no índice.
        """
        if not telemetry or not telemetry.get("times"):
            return None

        from core.session_manager import parse_lap_time_ms  # import tardio: ciclo

        now = datetime.now()
        stamp = now.strftime("%Y%m%d-%H%M%S")
        base_id = _SAFE_ID.sub("_", f"{stamp}_L{lap_number:03d}"
                                if lap_number else stamp)
        lap_id, rel_file = self._unique_lap_id(track, car, base_id)

        if not session_type and extra_metadata and "session_type" in extra_metadata:
            session_type = str(extra_metadata.get("session_type") or "")

        meta = {
            "track": clean_name(track, "UnknownTrack"),
            "car": clean_name(car, "UnknownCar"),
            "lap_time_str": lap_time_str,
            "sector_times_ms": list(sector_times_ms)[:3],
            "timestamp": now.isoformat(timespec="seconds"),
            "lap_number": lap_number,
            "session_id": session_id,
            "session_type": session_type,
            "manual_save": manual,
            "full_lap": full_lap,
            "valid": valid,
            "pit_lap": pit_lap,
        }
        if extra_metadata:
            meta.update(extra_metadata)
            if "session_type" in extra_metadata and not meta.get("session_type"):
                meta["session_type"] = str(extra_metadata["session_type"] or "")

        payload = {"metadata": meta, "telemetry": compact_telemetry(telemetry)}
        blob = gzip.compress(
            json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode("utf-8"),
            compresslevel=6)
        if not write_bytes_atomic(os.path.join(self.folder_for(track, car), rel_file), blob):
            return None

        rec = LapRecord(
            lap_id=lap_id,
            file=rel_file.replace("\\", "/"),
            track=meta["track"], car=meta["car"],
            session_id=session_id,
            session_type=meta.get("session_type", ""),
            lap_number=lap_number,
            lap_time_str=lap_time_str,
            lap_time_ms=parse_lap_time_ms(lap_time_str),
            sector_times_ms=meta["sector_times_ms"] + [0] * (3 - len(meta["sector_times_ms"])),
            timestamp=meta["timestamp"],
            points=len(telemetry.get("times", [])),
            channels=sorted(k for k, v in telemetry.items() if isinstance(v, list) and v),
            full_lap=full_lap, valid=valid, pit_lap=pit_lap,
            manual_save=manual,
        )

        recs = self.records(track, car)
        recs.insert(0, rec)
        self._telemetry_cache[self._cache_key(track, car, rec)] = payload["telemetry"]
        self._trim_cache()

        removed = self._apply_retention(track, car, recs)
        self._write_index(track, car, recs)
        if removed:
            print(f"[LapLibrary] Retenção: {removed} volta(s) antiga(s) removida(s).")
        return rec

    # -- leitura sob demanda -------------------------------------------------

    def _cache_key(self, track: str, car: str, rec: LapRecord) -> str:
        return f"{self._key(track, car)}|{rec.lap_id}"

    def _trim_cache(self) -> None:
        while len(self._telemetry_cache) > self.CACHE_SIZE:
            self._telemetry_cache.popitem(last=False)

    def load_telemetry(self, track: str, car: str,
                       rec: LapRecord) -> Optional[dict]:
        """Telemetria completa de uma volta do índice (None se ilegível)."""
        if rec is None or not rec.file:
            return None
        key = self._cache_key(track, car, rec)
        cached = self._telemetry_cache.get(key)
        if cached is not None:
            self._telemetry_cache.move_to_end(key)
            return cached
        path = os.path.join(self.folder_for(track, car), rec.file)
        if not os.path.exists(path):
            return None
        data = read_json_file(path)
        if not isinstance(data, dict) or "telemetry" not in data:
            return None
        telemetry = data["telemetry"]
        self._telemetry_cache[key] = telemetry
        self._trim_cache()
        return telemetry

    def load_ghost(self, track: str, car: str,
                   rec: LapRecord) -> Optional[dict]:
        """
        A volta no formato de ghost que o resto do app já consome:
        ``{"metadata": {...}, "telemetry": {...}}``.
        """
        telemetry = self.load_telemetry(track, car, rec)
        if telemetry is None:
            return None
        return {
            "metadata": {
                "track": rec.track, "car": rec.car,
                "lap_time_str": rec.lap_time_str,
                "sector_times_ms": list(rec.sector_times_ms),
                "timestamp": rec.timestamp,
                "lap_number": rec.lap_number,
                "session_id": rec.session_id,
                "full_lap": rec.full_lap,
                "valid": rec.valid,
                "pit_lap": rec.pit_lap,
                "lap_id": rec.lap_id,
            },
            "telemetry": telemetry,
        }

    # -- consultas -----------------------------------------------------------

    def personal_best(self, track: str, car: str,
                      *, require_valid: bool = True) -> Optional[LapRecord]:
        """
        Volta mais rápida que serve de referência: inteira, com tempo e —
        por padrão — limpa.

        Ao contrário do antigo `best_lap_ghost.json`, aqui o Personal Best é
        DERIVADO do catálogo. Bater o recorde não apaga o anterior: ele
        continua na lista, e você pode voltar a usá-lo como referência.
        """
        candidates = [r for r in self.records(track, car)
                      if r.is_reference_material and (r.valid or not require_valid)]
        if not candidates:
            return None
        return min(candidates, key=lambda r: r.lap_time_ms)

    def best_lap(self, track: str, car: str,
                 *, require_valid: bool = True) -> Optional[LapRecord]:
        """Atalho de conveniência para personal_best."""
        return self.personal_best(track, car, require_valid=require_valid)

    def sessions(self, track: str, car: str) -> List[dict]:
        """
        Voltas agrupadas por sessão, da mais recente para a mais antiga.

        Cada item: ``{"session_id", "date_str", "laps": [LapRecord, ...],
        "best": LapRecord|None}``.
        """
        grouped: "OrderedDict[str, List[LapRecord]]" = OrderedDict()
        for rec in self.records(track, car):
            grouped.setdefault(rec.session_id or rec.timestamp[:10], []).append(rec)
        out = []
        for sid, laps in grouped.items():
            timed = [r for r in laps if r.lap_time_ms > 0 and r.full_lap]
            out.append({
                "session_id": sid,
                "date_str": laps[0].date_str if laps else "",
                "laps": laps,
                "best": min(timed, key=lambda r: r.lap_time_ms) if timed else None,
            })
        return out

    def catalog(self) -> List[dict]:
        """
        Todas as combinações Pista/Carro que existem em disco.

        Cada item: ``{"track", "car", "laps": int, "best": LapRecord|None,
        "bytes": int}``. Ordena por pista e carro. É o que a tela de análise
        pós-sessão usa para montar a árvore sem abrir telemetria nenhuma.
        """
        out: List[dict] = []
        if not os.path.isdir(self.data_dir):
            return out
        for track in sorted(os.listdir(self.data_dir)):
            track_dir = os.path.join(self.data_dir, track)
            if not os.path.isdir(track_dir):
                continue
            for car in sorted(os.listdir(track_dir)):
                if not os.path.isdir(os.path.join(track_dir, car)):
                    continue
                recs = self.records(track, car)
                if not recs:
                    continue
                timed = [r for r in recs if r.is_reference_material]
                out.append({
                    "track": track,
                    "car": car,
                    "laps": len(recs),
                    "best": min(timed, key=lambda r: r.lap_time_ms) if timed else None,
                    "bytes": self.disk_usage(track, car),
                })
        return out

    def all_records(self) -> List[LapRecord]:
        """Devolve todas as voltas registradas em todas as pistas e carros."""
        all_recs: List[LapRecord] = []
        if not os.path.isdir(self.data_dir):
            return all_recs
        for track in sorted(os.listdir(self.data_dir)):
            track_dir = os.path.join(self.data_dir, track)
            if not os.path.isdir(track_dir):
                continue
            for car in sorted(os.listdir(track_dir)):
                if not os.path.isdir(os.path.join(track_dir, car)):
                    continue
                all_recs.extend(self.records(track, car))
        return all_recs

    def disk_usage(self, track: str = "", car: str = "") -> int:
        """Bytes ocupados pelas voltas (a pasta toda quando sem argumentos)."""
        root = self.folder_for(track, car) if track or car else self.data_dir
        total = 0
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    pass
        return total

    # -- exportação ----------------------------------------------------------

    #: Ordem das colunas no CSV. Canal que não estiver aqui vai depois, em
    #: ordem alfabética — nenhum dado fica de fora por não ser conhecido.
    CSV_COLUMN_ORDER = (
        "times", "distance", "speed", "gas", "brake", "gear", "rpm", "steer",
        "delta", "sector", "g_lat", "g_lon",
        "abs_intervention", "tc_intervention", "car_x", "car_y", "car_z",
    )

    def export_csv(self, track: str, car: str, rec: LapRecord,
                   path: str) -> bool:
        """
        Grava a volta como CSV, uma linha por amostra.

        Serve para abrir a volta em planilha, no Python ou em qualquer
        ferramenta de análise — o app gravava só PNG da tela até aqui, o que
        não dá para cruzar com nada.
        """
        telemetry = self.load_telemetry(track, car, rec)
        if not telemetry:
            return False
        known = [c for c in self.CSV_COLUMN_ORDER if telemetry.get(c)]
        extra = sorted(k for k, v in telemetry.items()
                       if isinstance(v, list) and v and k not in known)
        columns = known + extra
        n = min(len(telemetry[c]) for c in columns) if columns else 0
        if not n:
            return False
        try:
            with open(path, 'w', encoding='utf-8', newline='') as f:
                # `csv` não entra em cena por um motivo só: estas colunas são
                # números e nomes de canal, sem vírgula nem aspas para escapar.
                f.write(",".join(columns) + "\n")
                for i in range(n):
                    f.write(",".join(str(telemetry[c][i]) for c in columns) + "\n")
            return True
        except OSError as e:
            print(f"[LapLibrary] Falha ao exportar CSV: {e}")
            return False

    def export_motec(self, track: str, car: str, rec: LapRecord,
                     path: str) -> bool:
        """
        Grava a volta no formato MoTeC i2 (.ld) com arquivo de marcas (.ldx).

        Permite abrir e analisar a telemetria no software MoTeC i2 Pro.
        """
        telemetry = self.load_telemetry(track, car, rec)
        if not telemetry:
            return False
        try:
            from core.motec import MotecExporter
            exporter = MotecExporter()
            lap_time_s = float(rec.lap_time_ms) / 1000.0 if rec.lap_time_ms > 0 else None
            sector_times_s = (
                [float(s) / 1000.0 for s in rec.sector_times_ms]
                if rec.sector_times_ms and any(s > 0 for s in rec.sector_times_ms)
                else None
            )
            return exporter.export(
                telemetry,
                path,
                track=track or rec.track,
                car=car or rec.car,
                lap_number=rec.lap_number,
                lap_time_s=lap_time_s,
                sector_times_s=sector_times_s,
                comment=f"ApexView - Lap {rec.lap_number} ({rec.lap_time_str})",
            )
        except Exception as e:
            print(f"[LapLibrary] Falha ao exportar MoTeC: {e}")
            return False

    def generate_lap_report(self, track: str, car: str, rec: LapRecord,
                            ref_rec: Optional[LapRecord] = None,
                            format: str = "md") -> Optional[str]:
        """
        Gera o texto completo do relatório de desempenho da volta.
        Se ref_rec não for informado, usa o Personal Best (melhor volta da pista/carro)
        como referência automática se houver uma volta diferente de rec.
        """
        lap_telemetry = self.load_telemetry(track, car, rec)
        if not lap_telemetry:
            return None

        # Se não informou ref_rec, tenta buscar o melhor tempo válido da pista/carro
        if ref_rec is None:
            best = self.best_lap(track, car)
            if best is not None and best.lap_id != rec.lap_id:
                ref_rec = best

        ref_telemetry = self.load_telemetry(track, car, ref_rec) if ref_rec else None

        from core.lap_report import LapReportGenerator
        generator = LapReportGenerator()
        res = generator.analyze(
            lap_telemetry,
            ref_telemetry=ref_telemetry,
            track_name=track or rec.track,
            car_name=car or rec.car,
            lap_number=rec.lap_number,
            lap_time_str=rec.lap_time_str,
            lap_time_ms=rec.lap_time_ms,
            ref_lap_time_str=ref_rec.lap_time_str if ref_rec else "",
            ref_lap_time_ms=ref_rec.lap_time_ms if ref_rec else 0,
            sector_times_ms=rec.sector_times_ms,
            ref_sector_times_ms=ref_rec.sector_times_ms if ref_rec else None,
            date_str=rec.date_str,
            is_valid=rec.valid
        )

        fmt = (format or "md").lower()
        if "txt" in fmt or "plain" in fmt or "text" in fmt:
            return generator.format_plain_text(res)
        return generator.format_markdown(res)

    def export_report(self, track: str, car: str, rec: LapRecord,
                      path: str, ref_rec: Optional[LapRecord] = None,
                      format: Optional[str] = None) -> bool:
        """
        Grava o relatório de desempenho em arquivo (.md ou .txt).
        Se format não for passado, detecta pela extensão de path (.txt ou .md).
        """
        if not format:
            format = "txt" if path.lower().endswith(".txt") else "md"

        content = self.generate_lap_report(track, car, rec, ref_rec=ref_rec, format=format)
        if content is None:
            return False

        try:
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except OSError as e:
            print(f"[LapLibrary] Falha ao exportar relatório: {e}")
            return False

    def export_lap_file(self, track: str, car: str, rec: LapRecord,
                        output_path: str) -> bool:
        """
        Empacota a telemetria .json.gz e os metadados do índice em um arquivo portátil
        .apex (ou .lap.json.gz).
        """
        telemetry = self.load_telemetry(track, car, rec)
        if not telemetry:
            print(f"[LapLibrary] Telemetria não encontrada para volta {rec.lap_id}")
            return False

        if not (output_path.lower().endswith(".apex") or
                output_path.lower().endswith(".json.gz") or
                output_path.lower().endswith(".json")):
            output_path += ".apex"

        payload = {
            "format": "apex_lap_package",
            "schema_version": 1,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "metadata": rec.to_dict(),
            "telemetry": compact_telemetry(telemetry)
        }

        try:
            parent = os.path.dirname(os.path.abspath(output_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
            blob = gzip.compress(
                json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode("utf-8"),
                compresslevel=6
            )
            return write_bytes_atomic(output_path, blob)
        except Exception as e:
            print(f"[LapLibrary] Erro ao exportar pacote .apex: {e}")
            return False

    def import_lap_file(self, file_path: str) -> Optional[LapRecord]:
        """
        Valida a integridade, descompacta os metadados, detecta pista e carro,
        e salva a volta dentro da pasta correta em telemetry_data/<Pista>/<Carro>/laps/,
        adicionando o registro ao index.json local com a flag 'imported': True e
        marcando-a como protegida/pin (não sujeita à retenção automática).
        """
        if not os.path.isfile(file_path):
            print(f"[LapLibrary] Arquivo de importação inexistente: {file_path}")
            return None

        data = read_json_file(file_path)
        if not isinstance(data, dict):
            print(f"[LapLibrary] Arquivo de volta inválido ou ilegível: {file_path}")
            return None

        if "metadata" in data and "telemetry" in data:
            meta = data.get("metadata", {}) or {}
            telemetry = data.get("telemetry", {}) or {}
        elif "telemetry" in data:
            meta = data.get("metadata", {}) or {}
            telemetry = data.get("telemetry", {}) or {}
        else:
            print(f"[LapLibrary] Arquivo não possui bloco de telemetria: {file_path}")
            return None

        times = telemetry.get("times")
        if not times or not isinstance(times, list) or len(times) < 5:
            print(f"[LapLibrary] Telemetria sem amostras de tempo suficientes: {file_path}")
            return None

        from core.session_manager import parse_lap_time_ms

        raw_track = str(meta.get("track") or "UnknownTrack")
        raw_car = str(meta.get("car") or "UnknownCar")
        track = clean_name(raw_track, "UnknownTrack")
        car = clean_name(raw_car, "UnknownCar")

        folder = self.folder_for(track, car)
        laps_dir = os.path.join(folder, "laps")
        os.makedirs(laps_dir, exist_ok=True)

        now_str = datetime.now().strftime("%Y%m%d-%H%M%S")
        lap_num = int(meta.get("lap_number", 0) or 0)
        base_id = _SAFE_ID.sub("_", f"imp_{now_str}_L{lap_num:03d}" if lap_num else f"imp_{now_str}")
        lap_id, rel_file = self._unique_lap_id(track, car, base_id)

        lap_time_str = str(meta.get("lap_time_str") or "--:--.---")
        sector_times_ms = list(meta.get("sector_times_ms", [0, 0, 0]))[:3]
        if len(sector_times_ms) < 3:
            sector_times_ms += [0] * (3 - len(sector_times_ms))

        imported_meta = dict(meta)
        imported_meta.update({
            "track": track,
            "car": car,
            "lap_id": lap_id,
            "lap_time_str": lap_time_str,
            "sector_times_ms": sector_times_ms,
            "timestamp": meta.get("timestamp") or datetime.now().isoformat(timespec="seconds"),
            "lap_number": lap_num,
            "session_id": str(meta.get("session_id") or "imported"),
            "session_type": str(meta.get("session_type") or "Imported"),
            "full_lap": bool(meta.get("full_lap", True)),
            "valid": bool(meta.get("valid", True)),
            "pit_lap": bool(meta.get("pit_lap", False)),
            "manual_save": True,
            "pinned": True,
            "imported": True,
        })

        lap_payload = {
            "metadata": imported_meta,
            "telemetry": compact_telemetry(telemetry)
        }

        dest_file = os.path.join(folder, rel_file)
        blob = gzip.compress(
            json.dumps(lap_payload, ensure_ascii=False, separators=(',', ':')).encode("utf-8"),
            compresslevel=6
        )
        if not write_bytes_atomic(dest_file, blob):
            print(f"[LapLibrary] Falha ao salvar telemetria importada em {dest_file}")
            return None

        rec = LapRecord(
            lap_id=lap_id,
            file=rel_file.replace("\\", "/"),
            track=track,
            car=car,
            session_id=imported_meta["session_id"],
            session_type=imported_meta["session_type"],
            lap_number=lap_num,
            lap_time_str=lap_time_str,
            lap_time_ms=parse_lap_time_ms(lap_time_str),
            sector_times_ms=sector_times_ms,
            timestamp=imported_meta["timestamp"],
            points=len(times),
            channels=sorted(k for k, v in telemetry.items() if isinstance(v, list) and v),
            full_lap=imported_meta["full_lap"],
            valid=imported_meta["valid"],
            pit_lap=imported_meta["pit_lap"],
            manual_save=True,
            pinned=True,
            imported=True,
        )

        recs = self.records(track, car)
        recs.insert(0, rec)
        self._write_index(track, car, recs)
        self._telemetry_cache[self._cache_key(track, car, rec)] = lap_payload["telemetry"]
        self._trim_cache()
        print(f"[LapLibrary] Volta {lap_id} importada com sucesso para {track}/{car}")
        return rec

    # -- manutenção ----------------------------------------------------------

    def set_pinned(self, track: str, car: str, lap_id: str,
                   pinned: bool = True) -> bool:
        """Protege (ou desprotege) uma volta da limpeza automática."""
        rec = self.find(track, car, lap_id)
        if rec is None:
            return False
        rec.pinned = pinned
        self._write_index(track, car, self.records(track, car))
        return True

    def delete(self, track: str, car: str, lap_id: str) -> bool:
        """Apaga uma volta do disco e do índice."""
        recs = self.records(track, car)
        rec = next((r for r in recs if r.lap_id == lap_id), None)
        if rec is None:
            return False
        self._remove_file(track, car, rec)
        recs.remove(rec)
        self._telemetry_cache.pop(self._cache_key(track, car, rec), None)
        self._write_index(track, car, recs)
        return True

    def _remove_file(self, track: str, car: str, rec: LapRecord) -> None:
        try:
            path = os.path.join(self.folder_for(track, car), rec.file)
            if os.path.exists(path):
                os.remove(path)
        except OSError as e:
            print(f"[LapLibrary] Não consegui apagar {rec.file}: {e}")

    def prune(self, track: str, car: str) -> int:
        """Aplica a retenção agora e grava o índice. Devolve quantas saíram."""
        recs = self.records(track, car)
        removed = self._apply_retention(track, car, recs)
        if removed:
            self._write_index(track, car, recs)
        return removed

    def _apply_retention(self, track: str, car: str,
                         recs: List[LapRecord]) -> int:
        """
        Remove do disco e da lista (in-place) as voltas que não se salvam por
        nenhum critério. Ver `RetentionPolicy`.
        """
        pol = self.retention
        if not pol.enabled or not recs:
            return 0

        keep_ids = set()
        # Fixadas, salvas à mão e importadas: intocáveis
        keep_ids.update(r.lap_id for r in recs if r.pinned or r.manual_save or getattr(r, "imported", False))
        # As mais recentes (a lista já vem da mais nova para a mais velha)
        keep_ids.update(r.lap_id for r in recs[:pol.keep_recent])
        # As mais rápidas válidas e inteiras
        timed = sorted((r for r in recs if r.is_reference_material and r.valid),
                       key=lambda r: r.lap_time_ms)
        keep_ids.update(r.lap_id for r in timed[:pol.keep_best])

        doomed = [r for r in recs if r.lap_id not in keep_ids]
        for rec in doomed:
            self._remove_file(track, car, rec)
            self._telemetry_cache.pop(self._cache_key(track, car, rec), None)
            recs.remove(rec)
        return len(doomed)
