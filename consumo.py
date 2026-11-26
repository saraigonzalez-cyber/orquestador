#!/usr/bin/env python3
"""
Resume el consumo del orquestador: una fila por llamada a Claude, sacada de los JSON de logs/.

Uso (desde la carpeta de vuestro repo):
    python3 ../consumo.py                 # lee ../logs y escribe ../consumo.csv (fuera de vuestro repo)
    python3 ../consumo.py --logs RUTA     # otra carpeta de logs

coste_cc_usd es la estimación de Claude Code; con OpenRouter suele salir unas 3 veces más alta
que lo cobrado. El gasto real está en la línea «Gasto» de orquestador.log y en Activity de OpenRouter.
"""
import argparse
import csv
import glob
import json
import os
from collections import defaultdict

ap = argparse.ArgumentParser()
ap.add_argument("--logs", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs"))
ap.add_argument("--salida", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "consumo.csv"))
a = ap.parse_args()

filas = []
for f in sorted(glob.glob(os.path.join(a.logs, "*", "*-F*.json"))):
    res = None
    for linea in open(f, encoding="utf-8"):
        try:
            ev = json.loads(linea)
        except ValueError:
            continue
        if ev.get("type") == "result" or "total_cost_usd" in ev:
            res = ev
    if not res:
        continue
    partes = os.path.basename(f)[:-5].split("-")          # 075438-F08-impl1[-cortada]
    if len(partes) < 3:
        continue
    mu = res.get("modelUsage") or {}
    tok = lambda k: sum(int(v.get(k) or 0) for v in mu.values())
    filas.append([os.path.basename(os.path.dirname(f)), partes[0], partes[1], partes[2],
                  round((res.get("duration_ms") or 0) / 1000), round(res.get("total_cost_usd") or 0, 4),
                  tok("inputTokens"), tok("outputTokens"), tok("cacheReadInputTokens"),
                  tok("cacheCreationInputTokens"), "+".join(mu) or "?"])

with open(a.salida, "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["ejecucion", "hora", "func", "fase", "segundos", "coste_cc_usd", "tokens_in", "tokens_out",
                "cache_leidos", "cache_escritos", "modelos"])
    w.writerows(filas)

res = defaultdict(lambda: [0, 0, 0.0, 0, 0])   # llamadas, segundos, coste, tokens leídos, tokens escritos
for r in filas:
    x = res[r[2]]
    x[0] += 1; x[1] += r[4]; x[2] += r[5]; x[3] += r[6] + r[8] + r[9]; x[4] += r[7]
print(f"{len(filas)} llamadas en {a.salida}\n")
print(f"{'func':<5} {'llam.':>5} {'min':>5} {'USD cc':>7} {'M leídos':>9} {'k escritos':>10}")
for fid in sorted(res):
    n, s, c, ti, to = res[fid]
    print(f"{fid:<5} {n:>5} {s / 60:>5.0f} {c:>7.2f} {ti / 1e6:>9.1f} {to / 1e3:>10.0f}")
