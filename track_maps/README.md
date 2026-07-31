# track_maps/ — Mapeamento de curvas por pista

Cada arquivo descreve onde começa e termina cada curva de uma pista. É o que
alimenta o painel **Curva a curva** do dashboard.

## Qual arquivo é usado

O nome do arquivo é o slug do nome da pista (minúsculas, tudo que não é letra
ou número virando `_`). Para `Autodromo Jose Carlos Pace`:

| Arquivo | Origem | Prioridade |
|---|---|---|
| `autodromo_jose_carlos_pace.json` | mapeamento manual, escrito por você | 1ª |
| `autodromo_jose_carlos_pace.auto.json` | detectado pelo app (Força G lateral > 0.4 g) | 2ª |

Se a pista não tem nenhum dos dois, o app detecta as curvas na primeira volta
válida e grava o `.auto.json` — assim a numeração não muda de volta para volta.
Para ajustar, **renomeie o `.auto.json` removendo o `.auto`**, dê nomes de
verdade às curvas e ele passa a ser o mapeamento manual (que sempre vence).

## Formato

```json
{
  "track": "Autodromo Jose Carlos Pace",
  "track_length": 4309.0,
  "corners": [
    {"name": "S do Senna", "start": 0.150, "end": 0.225, "direction": "L"},
    {"name": "Descida do Lago", "start_m": 2150, "end_m": 2480, "direction": "L"}
  ]
}
```

* `start` / `end` — posição relativa na pista, de `0.0` (linha de chegada) a `1.0`.
* `start_m` / `end_m` — alternativa em metros; exige `track_length`.
* `direction` — `"L"` ou `"R"`. Só decora o rótulo; é opcional.
* `name` — opcional, o padrão é `C1`, `C2`, ...

Curvas com limites inválidos (fora de 0–1, ou fim antes do começo) são
ignoradas em silêncio: uma linha torta não derruba o dashboard.

## Onde as métricas são calculadas

`core/corner_analysis.py`. Os limiares (10% de freio, 100% de acelerador,
0.4 g de detecção, janelas de busca) estão no topo do módulo, todos nomeados.
