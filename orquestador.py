#!/usr/bin/env python3
"""
Orquestador de Claude Code: implementa -> verifica -> revisa -> commit, funcionalidad a funcionalidad.

Uso:
    python orquestador.py --repo /ruta/al/repo --spec SPEC.md
    python orquestador.py --repo . --spec SPEC.md --solo F07     # una funcionalidad (aunque esté bloqueada)
    python orquestador.py --repo . --spec SPEC.md --dry-run      # comprueba la spec y muestra el plan

Spec en Markdown, escrita por una persona; el estado lo escribe el script en <nombre>.estado.json.

Requisitos: Python 3.9+, git, `claude` (Claude Code) y export OPENROUTER_API_KEY=sk-or-...
Aviso al móvil (opcional): export NTFY_TOPIC=mi-tema-secreto  (app ntfy en iOS/Android)
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- configuración
MAX_ITERACIONES = 5          # intentos por funcionalidad antes de marcarla "bloqueada"
MAX_TURNOS_IMPL = 100        # turnos agénticos por llamada al implementador (subido para funcionalidades grandes)
MAX_TURNOS_REV = 25          # turnos agénticos por llamada al revisor
MAX_USD_LLAMADA = 5.0        # tope de gasto por llamada a claude
MAX_USD_TOTAL = 40.0         # tope de gasto de toda la ejecución
TIMEOUT_LLAMADA = 30 * 60    # segundos
TIMEOUT_VERIF = 10 * 60      # segundos por comando de verificación

# ---------------------------------------------------------------- modelos
USAR_OPENROUTER = True       # Claude Code habla con OpenRouter en vez de con Anthropic
MODELOS_OPENROUTER = {       # alias de Claude Code -> modelo en OpenRouter
    "opus": "~anthropic/claude-opus-latest",
    "sonnet": "~anthropic/claude-sonnet-latest",
    "haiku": "~anthropic/claude-haiku-latest",
}
MODELO_IMPL = "sonnet"       # "opus", "sonnet" o "haiku"
MODELO_REV = None            # None = el mismo que el implementador

HERRAMIENTAS_BASE_IMPL = ["Read", "Edit", "Write", "Glob", "Grep", "Bash(git status:*)", "Bash(git diff:*)", "Bash(ls:*)"]
HERRAMIENTAS_REV = ["Read", "Glob", "Grep"]  # el revisor solo lee

PROMPT_IMPLEMENTADOR = """Eres el programador de este repositorio. Implementa SOLO esta funcionalidad.

## Contexto del proyecto
{contexto}

## Funcionalidad {id}: {titulo}
{descripcion}

## Criterios de aceptación (todos obligatorios)
{criterios}

## Reglas
- Escribe o amplía los tests que demuestren cada criterio.
- No debilites, borres ni marques como skip tests existentes para que pasen.
- No toques nada fuera del alcance de esta funcionalidad.
- No hagas git commit ni cambies de rama: de eso se encarga el orquestador.
- Ejecuta tú mismo la verificación antes de terminar: {verificacion}
- Termina con un resumen breve de lo que has cambiado y por qué.
"""

PROMPT_CORRECCION = """El orquestador ha revisado tu trabajo en {id} y NO está aprobado.

{feedback}

Corrige exactamente esto, vuelve a ejecutar la verificación ({verificacion}) y resume los cambios."""

PROMPT_REVISOR = """Eres un revisor de código senior, exigente y escéptico. No has escrito este código.
Decide si el diff cumple la funcionalidad. Puedes leer ficheros del repo para tener contexto.

## Funcionalidad {id}: {titulo}
{descripcion}

## Criterios de aceptación
{criterios}

## Resultado de la verificación automática
Todos los comandos pasaron: {verificacion}

## Diff a revisar
```diff
{diff}
```

Comprueba: que se cumple cada criterio; que hay tests que realmente lo demuestran; que no se han
debilitado tests; bugs evidentes, casos límite, seguridad; cambios fuera de alcance.
No rechaces por cuestiones de estilo menores.
En "problemas" pon SOLO lo que impide aprobar. Las sugerencias no bloqueantes, en el análisis, no en el JSON.

Responde con tu análisis y TERMINA con un bloque JSON exactamente así:
```json
{{"aprobado": true/false, "problemas": ["problema concreto y accionable", "..."]}}
```"""

# ---------------------------------------------------------------- utilidades
GASTO = {"total": 0.0, "base": None}   # base: uso de la clave de OpenRouter al empezar
LOG_DIR: Path = Path(".")
ENTORNO = None                # variables de entorno con las que se lanza claude (ver preparar_entorno)


def log(msg: str) -> None:
    linea = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(linea, flush=True)
    with open(LOG_DIR / "orquestador.log", "a", encoding="utf-8") as f:
        f.write(linea + "\n")


def sh(cmd, cwd, timeout=120, shell=False, env=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, shell=shell, env=env)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def git(repo, *args):
    code, out = sh(["git", *args], cwd=repo)
    if code != 0:
        raise RuntimeError(f"git {' '.join(args)} falló:\n{out}")
    return out


def avisar(titulo: str, cuerpo: str) -> None:
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    try:
        req = urllib.request.Request(f"https://ntfy.sh/{topic}", data=cuerpo.encode()[:3500],
                                     headers={"Title": titulo.encode("ascii", "ignore").decode()})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:  # el aviso nunca debe tumbar la ejecución
        log(f"No se pudo enviar el aviso: {e}")


def preparar_entorno():
    """Devuelve el entorno para lanzar claude. Con OpenRouter, apunta Claude Code a su endpoint."""
    env = dict(os.environ)
    if not USAR_OPENROUTER:
        return env
    clave = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not clave.startswith("sk-or-"):
        sys.exit("Falta OPENROUTER_API_KEY (empieza por sk-or-). Haz: export OPENROUTER_API_KEY=sk-or-...")
    env.update({
        "ANTHROPIC_BASE_URL": "https://openrouter.ai/api",
        "ANTHROPIC_AUTH_TOKEN": clave,
        "ANTHROPIC_API_KEY": "",  # vacía a propósito: así no usa una clave de Anthropic
        "ANTHROPIC_DEFAULT_OPUS_MODEL": MODELOS_OPENROUTER["opus"],
        "ANTHROPIC_DEFAULT_SONNET_MODEL": MODELOS_OPENROUTER["sonnet"],
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": MODELOS_OPENROUTER["haiku"],
        "CLAUDE_CODE_SUBAGENT_MODEL": MODELOS_OPENROUTER.get(MODELO_IMPL, MODELO_IMPL),
    })
    return env


def credito_openrouter():
    """(gastado, restante) de la clave en OpenRouter, en USD. None si no se puede saber."""
    if not USAR_OPENROUTER:
        return None, None
    try:
        req = urllib.request.Request("https://openrouter.ai/api/v1/key",
                                     headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"})
        d = json.load(urllib.request.urlopen(req, timeout=15))["data"]
        return float(d.get("usage") or 0), d.get("limit_remaining")
    except Exception:
        return None, None


class ClaudeNoResponde(RuntimeError):
    """Claude no está disponible (sin sesión, clave mala, sin saldo...): no tiene sentido seguir."""


def comprobar_claude(repo, modelo=None):
    """Llamada mínima antes de empezar: si falla, paramos con un mensaje claro."""
    cmd = ["claude", "-p", "Responde solo: OK", "--output-format", "json", "--max-turns", "1"]
    if modelo:
        cmd += ["--model", modelo]
    try:
        code, out = sh(cmd, cwd=repo, timeout=120, env=ENTORNO)
    except FileNotFoundError:
        raise ClaudeNoResponde("No encuentro el comando claude. ¿Está instalado Claude Code y en el PATH?")
    try:
        data = json.loads(out.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        raise ClaudeNoResponde(f"claude no responde bien (código {code}):\n{out[-1500:]}")
    if data.get("is_error"):
        raise ClaudeNoResponde(f"claude devuelve un error: {data.get('result') or data}")
    destino = "OpenRouter" if USAR_OPENROUTER else "Anthropic"
    nombre = MODELOS_OPENROUTER.get(modelo, modelo) if USAR_OPENROUTER else modelo
    log(f"Claude responde vía {destino} · modelo: {nombre or 'el de por defecto'}")
    uso, restante = credito_openrouter()
    GASTO["base"] = uso
    if restante is not None:
        log(f"Crédito restante de la clave en OpenRouter: {float(restante):.2f} USD")


HERRAMIENTAS_QUE_SE_VEN = {"Edit", "Write", "MultiEdit", "Bash"}   # el resto (leer, buscar) solo cuenta
AVISO_CADA = 60                                                    # segundos sin nada que contar
TIMEOUT_INACTIVIDAD = 10 * 60                                      # sin ninguna señal de claude: se corta


def describir_accion(bloque, repo):
    """Texto corto para el log a partir de una llamada a herramienta de Claude Code."""
    nombre, entrada = bloque.get("name", "?"), bloque.get("input") or {}
    detalle = entrada.get("file_path") or entrada.get("command") or entrada.get("pattern") or ""
    if isinstance(detalle, str) and detalle.startswith(repo):
        detalle = detalle[len(repo):].lstrip("/")
    return f"{nombre} {str(detalle).splitlines()[0][:90] if detalle else ''}".rstrip()


def ejecutar_con_avance(cmd, repo, etiqueta):
    """Lanza claude en modo stream-json y va escribiendo en el log lo que hace.
    Devuelve (código, salida_completa, evento_result)."""
    proc = subprocess.Popen(cmd, cwd=repo, env=ENTORNO, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
    lineas, resultado, acciones = [], None, 0
    t0 = ultimo_aviso = time.time()
    vivo = {"ultimo": t0, "motivo": None, "fin": False}

    def vigilar():  # corta la llamada si dura demasiado o si deja de dar señales
        while not vivo["fin"]:
            ahora = time.time()
            if ahora - t0 > TIMEOUT_LLAMADA:
                vivo["motivo"] = f"superó el tiempo límite de {TIMEOUT_LLAMADA // 60} min"
            elif ahora - vivo["ultimo"] > TIMEOUT_INACTIVIDAD:
                vivo["motivo"] = f"lleva {TIMEOUT_INACTIVIDAD // 60} min sin dar señales (¿un comando colgado?)"
            if vivo["motivo"]:
                try:  # mata claude y todo lo que haya lanzado (pytest, un servidor...)
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
                return
            time.sleep(5)

    threading.Thread(target=vigilar, daemon=True).start()
    try:
        for linea in proc.stdout:
            vivo["ultimo"] = time.time()
            lineas.append(linea)
            try:
                ev = json.loads(linea)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "result":
                resultado = ev
            elif ev.get("type") == "assistant":
                for bloque in (ev.get("message") or {}).get("content") or []:
                    if bloque.get("type") != "tool_use":
                        continue
                    acciones += 1
                    if bloque.get("name") in HERRAMIENTAS_QUE_SE_VEN:
                        log(f"    {etiqueta} › {describir_accion(bloque, repo)}")
                        ultimo_aviso = time.time()
            if time.time() - ultimo_aviso >= AVISO_CADA:
                log(f"    {etiqueta} … sigue trabajando ({time.time() - t0:.0f}s, {acciones} acciones)")
                ultimo_aviso = time.time()
        proc.wait()
    finally:
        vivo["fin"] = True
    if vivo["motivo"]:
        (LOG_DIR / f"{datetime.now():%H%M%S}-{etiqueta}-cortada.json").write_text("".join(lineas), encoding="utf-8")
        raise RuntimeError(f"{etiqueta} {vivo['motivo']}")
    return proc.returncode, "".join(lineas), resultado


def claude(prompt, repo, herramientas, max_turnos, modelo=None, permiso="acceptEdits", resume=None, etiqueta=""):
    """Llama a `claude -p` y devuelve (texto_resultado, session_id)."""
    if GASTO["total"] >= MAX_USD_TOTAL:
        raise RuntimeError(f"Tope de gasto total alcanzado ({GASTO['total']:.2f} USD)")
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--permission-mode", permiso, "--max-turns", str(max_turnos),
           "--max-budget-usd", str(MAX_USD_LLAMADA)]
    if modelo:
        cmd += ["--model", modelo]
    if resume:
        cmd += ["--resume", resume]
    cmd += ["--allowedTools", *herramientas]  # al final: es un argumento de varios valores
    t0 = time.time()
    code, out, data = ejecutar_con_avance(cmd, repo, etiqueta)
    (LOG_DIR / f"{datetime.now():%H%M%S}-{etiqueta}.json").write_text(out, encoding="utf-8")
    if data is None:
        raise RuntimeError(f"Salida de claude no parseable (código {code}):\n{out[-2000:]}")
    coste = float(data.get("total_cost_usd") or 0)   # estimación de Claude Code (puede exagerar)
    uso, _ = credito_openrouter()                      # lo que de verdad ha cobrado OpenRouter
    if GASTO["base"] is not None and uso is not None:
        real = uso - GASTO["base"]                     # gasto real desde el inicio de la ejecución
        coste = max(0.0, real - GASTO["total"])        # OpenRouter puede tardar unos segundos en apuntarlo
        GASTO["total"] = real
    else:
        GASTO["total"] += coste
    if data.get("is_error") and not str(data.get("subtype", "")).startswith("error_max"):
        raise ClaudeNoResponde(f"claude devuelve un error: {data.get('result') or data}")
    log(f"  {etiqueta}: {time.time() - t0:.0f}s, {coste:.2f} USD (acumulado {GASTO['total']:.2f})")
    return data.get("result") or "", data.get("session_id")


def verificar(repo, comandos):
    """Ejecuta los comandos de verificación. Devuelve (ok, informe)."""
    for c in comandos:
        try:
            code, out = sh(c, cwd=repo, timeout=TIMEOUT_VERIF, shell=True)
        except subprocess.TimeoutExpired:
            return False, f"`{c}` superó el tiempo límite de {TIMEOUT_VERIF}s"
        if code != 0:
            return False, f"Falla `{c}` (código {code}). Últimas líneas:\n```\n{out[-4000:]}\n```"
    return True, "ok"


def diff_actual(repo, limite=60_000):
    git(repo, "add", "-A")  # incluye ficheros nuevos en el diff
    d = git(repo, "diff", "--cached")
    return d if len(d) <= limite else d[:limite] + "\n... [diff truncado; lee los ficheros si lo necesitas]"


def parsear_veredicto(texto):
    """Busca el último objeto JSON con la clave "aprobado". Admite llaves dentro de los textos
    (p. ej. "/tickets/{id}"), que con una expresión regular rompían el veredicto."""
    dec = json.JSONDecoder()
    for i in reversed([m.start() for m in re.finditer(r"\{", texto)]):
        try:
            v, _ = dec.raw_decode(texto[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(v, dict) and "aprobado" in v:
            return bool(v.get("aprobado")), [str(p) for p in (v.get("problemas") or [])]
    return False, ["El revisor no devolvió un veredicto JSON válido; revisa de nuevo."]


# ---------------------------------------------------------------- spec en Markdown
RE_FUNC = re.compile(r"^##\s+([A-Za-z]+\d+)\s*[·:\-–—]\s*(.+?)\s*$")
RE_ITEM = re.compile(r"^\s*[-*]\s+(.*)$")


def _norm(t):
    t = t.strip().lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")):
        t = t.replace(a, b)
    return t


def _limpia(t):
    t = t.strip()
    return t[1:-1].strip() if len(t) > 1 and t[0] == t[-1] == "`" else t


def leer_spec_md(texto):
    """Convierte la spec en Markdown al diccionario que usa el bucle. Lanza ValueError con la línea del problema."""
    spec = {"proyecto": "", "contexto": "", "verificacion": [], "comandos_permitidos": [], "funcionalidades": []}
    seccion, func, en_criterios, errores = None, None, False, []
    contexto, desc = [], []

    def cerrar_func():
        if func is not None:
            func["descripcion"] = "\n".join(desc).strip()
            if not func["criterios"]:
                errores.append(f"línea {func['_linea']}: {func['id']} no tiene criterios (falta '### Criterios' con una lista)")
            spec["funcionalidades"].append(func)

    for n, linea in enumerate(texto.splitlines(), 1):
        if linea.startswith("# ") and not spec["proyecto"]:
            spec["proyecto"] = linea[2:].strip()
            continue
        if linea.startswith("## "):
            cerrar_func()
            func, en_criterios, desc = None, False, []
            m = RE_FUNC.match(linea)
            if m:
                func = {"id": m.group(1).upper(), "titulo": m.group(2), "criterios": [], "_linea": n}
                seccion = "funcionalidad"
            else:
                nombre = _norm(linea[3:])
                if nombre in ("contexto", "verificacion", "comandos permitidos"):
                    seccion = nombre
                else:
                    errores.append(f"línea {n}: sección desconocida «{linea.strip()}»")
                    seccion = None
            continue
        if seccion == "contexto":
            contexto.append(linea)
        elif seccion in ("verificacion", "comandos permitidos"):
            m = RE_ITEM.match(linea)
            if m:
                clave = "verificacion" if seccion == "verificacion" else "comandos_permitidos"
                spec[clave].append(_limpia(m.group(1)))
        elif seccion == "funcionalidad" and func is not None:
            if linea.startswith("### "):
                en_criterios = _norm(linea[4:]).startswith("criterios")
                continue
            if en_criterios:
                m = RE_ITEM.match(linea)
                if m:
                    func["criterios"].append(m.group(1).strip())
                elif linea.strip() and func["criterios"]:
                    func["criterios"][-1] += " " + linea.strip()   # continuación de un criterio largo
            else:
                desc.append(linea)
    cerrar_func()
    spec["contexto"] = "\n".join(contexto).strip()

    ids = [f["id"] for f in spec["funcionalidades"]]
    for i in sorted(set(x for x in ids if ids.count(x) > 1)):
        errores.append(f"el identificador {i} está repetido")
    if not spec["funcionalidades"]:
        errores.append("no hay ninguna funcionalidad (encabezados del tipo '## F01 · Título')")
    if not spec["verificacion"]:
        errores.append("falta la sección '## Verificación' con al menos un comando")
    if errores:
        raise ValueError("La spec tiene problemas:\n- " + "\n- ".join(errores))
    return spec


def cargar_spec(path: Path):
    texto = path.read_text(encoding="utf-8")
    if path.suffix == ".json":            # compatibilidad con specs antiguas
        return json.loads(texto)
    return leer_spec_md(texto)


def ruta_estado(spec_path: Path) -> Path:
    return spec_path.with_name(spec_path.name.split(".")[0] + ".estado.json")


def cargar_estado(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


# ---------------------------------------------------------------- bucle principal
def procesar(f, spec, repo):
    criterios = "\n".join(f"- {c}" for c in f["criterios"])
    verif = spec.get("verificacion", [])
    verif_txt = " && ".join(verif) or "(ninguna)"
    herramientas = HERRAMIENTAS_BASE_IMPL + spec.get("comandos_permitidos", [])
    base = git(repo, "rev-parse", "HEAD").strip()
    ctx = dict(id=f["id"], titulo=f["titulo"], descripcion=f.get("descripcion", ""),
               criterios=criterios, contexto=spec.get("contexto", ""), verificacion=verif_txt)

    sesion, feedback = None, None
    for i in range(1, MAX_ITERACIONES + 1):
        log(f"{f['id']} · iteración {i}/{MAX_ITERACIONES}")
        prompt = PROMPT_IMPLEMENTADOR.format(**ctx) if sesion is None else \
            PROMPT_CORRECCION.format(feedback=feedback, **ctx)
        _, sesion = claude(prompt, repo, herramientas, MAX_TURNOS_IMPL, MODELO_IMPL,
                           resume=sesion, etiqueta=f"{f['id']}-impl{i}")

        ok, informe = verificar(repo, verif)
        if not ok:
            utiles = [x for x in informe.splitlines() if x.strip() and not x.startswith("```")]
            log(f"  verificación KO: {utiles[0].split(' Últimas')[0][:120]}")
            if len(utiles) > 1:
                log(f"    · {utiles[-1][:200]}")  # la última línea suele decir qué ha fallado
            feedback = "La verificación automática falla:\n" + informe
            continue

        diff = diff_actual(repo)
        if not diff.strip():
            feedback = "No hay ningún cambio en el repositorio. Implementa la funcionalidad."
            continue

        texto, _ = claude(PROMPT_REVISOR.format(diff=diff, **ctx), repo, HERRAMIENTAS_REV,
                          MAX_TURNOS_REV, MODELO_REV, permiso="default", etiqueta=f"{f['id']}-rev{i}")
        aprobado, problemas = parsear_veredicto(texto)
        if aprobado:
            git(repo, "add", "-A")
            git(repo, "commit", "-m", f"{f['id']}: {f['titulo']}\n\nAprobado por revisor automático (iteración {i}).")
            log(f"  ✅ {f['id']} aprobada y commiteada")
            return "hecha", i
        log(f"  revisor rechaza: {len(problemas)} problema(s)")
        for p in problemas:
            log(f"    · {p[:300]}")
        feedback = "El revisor ha encontrado estos problemas:\n" + "\n".join(f"- {p}" for p in problemas)

    # agotados los intentos: guardar el trabajo como parche y dejar el repo limpio
    git(repo, "add", "-A")
    parche = LOG_DIR / f"{f['id']}-bloqueada.patch"
    parche.write_text(git(repo, "diff", "--cached", base), encoding="utf-8")
    git(repo, "reset", "--hard", base)
    git(repo, "clean", "-fd")
    log(f"  ⛔ {f['id']} bloqueada. Último feedback guardado; parche en {parche}")
    f["ultimo_feedback"] = feedback
    return "bloqueada", MAX_ITERACIONES


def main():
    global LOG_DIR
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--solo", help="ID de una funcionalidad concreta (se procesa aunque esté hecha o bloqueada)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    repo = str(Path(a.repo).resolve())
    spec_path = Path(a.spec).resolve()
    if not spec_path.exists():
        sys.exit(f"No encuentro la spec en {spec_path}")
    try:
        spec = cargar_spec(spec_path)
    except ValueError as e:
        sys.exit(str(e))
    estado_path = ruta_estado(spec_path)
    estado = cargar_estado(estado_path)
    if a.solo:   # --solo fuerza esa funcionalidad aunque esté hecha o bloqueada
        pendientes = [f for f in spec["funcionalidades"] if f["id"] == a.solo.upper()]
    else:
        pendientes = [f for f in spec["funcionalidades"]
                      if estado.get(f["id"], {}).get("estado", "pendiente") not in ("hecha", "bloqueada")]

    if a.dry_run:
        print(f"{spec['proyecto'] or spec_path.name}: {len(spec['funcionalidades'])} funcionalidades, "
              f"{len(pendientes)} pendientes. Verificación: {' && '.join(spec['verificacion'])}")
        for f in pendientes:
            print(f"  {f['id']}: {f['titulo']} ({len(f['criterios'])} criterios)")
        return

    LOG_DIR = spec_path.parent / "logs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # si spec y logs viven dentro del repo, que git los ignore (no deben entrar en los commits)
    for p in (spec_path, estado_path, spec_path.parent / "logs"):
        try:
            rel = p.relative_to(repo)
        except ValueError:
            continue
        excl = Path(repo) / ".git" / "info" / "exclude"
        actual = excl.read_text() if excl.exists() else ""
        if f"/{rel.as_posix()}\n" not in actual:
            excl.parent.mkdir(parents=True, exist_ok=True)
            excl.write_text(actual + f"/{rel.as_posix()}\n")
    if git(repo, "status", "--porcelain").strip():
        sys.exit("El repo tiene cambios sin commitear. Haz commit o stash antes de empezar.")

    log(f"Inicio: {len(pendientes)} funcionalidad(es) pendiente(s) en {repo}")
    global ENTORNO, MODELO_REV
    ENTORNO = preparar_entorno()
    MODELO_REV = MODELO_REV or MODELO_IMPL
    try:
        comprobar_claude(repo, MODELO_IMPL)
    except ClaudeNoResponde as e:
        log(f"⛔ {e}")
        sys.exit("Arregla la conexión con Claude y relanza. No se ha tocado nada.")
    resumen = []
    for f in pendientes:
        try:
            resultado, iters = procesar(f, spec, repo)
        except ClaudeNoResponde as e:  # no marcamos nada: sigue pendiente
            log(f"  ⛔ {e}")
            try:
                git(repo, "add", "-A")
                (LOG_DIR / f"{f['id']}-interrumpida.patch").write_text(git(repo, "diff", "--cached"), encoding="utf-8")
                git(repo, "reset", "--hard", "HEAD")
                git(repo, "clean", "-fd")
            except Exception:
                pass
            resumen.append(f"{f['id']}: sin tocar (Claude no responde)")
            break
        except Exception as e:
            log(f"  💥 error en {f['id']}: {e}")
            try:  # dejar el repo limpio para la siguiente funcionalidad
                git(repo, "add", "-A")
                (LOG_DIR / f"{f['id']}-error.patch").write_text(git(repo, "diff", "--cached"), encoding="utf-8")
                git(repo, "reset", "--hard", "HEAD")
                git(repo, "clean", "-fd")
            except Exception as e2:
                log(f"  no se pudo limpiar el repo: {e2}")
                break
            resultado, iters = "error", 0
            f["ultimo_feedback"] = str(e)[:2000]
            if "Tope de gasto" in str(e):
                resumen.append(f"{f['id']}: {resultado}")
                break
        estado[f["id"]] = {"estado": resultado, "iteraciones": iters, "fecha": f"{datetime.now():%Y-%m-%d %H:%M}",
                           "ultimo_feedback": f.get("ultimo_feedback", "")}
        estado_path.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")
        resumen.append(f"{f['id']}: {resultado} ({iters} it.)")

    texto = "\n".join(resumen) + f"\nGasto: {GASTO['total']:.2f} USD\nLogs: {LOG_DIR}"
    log("Fin.\n" + texto)
    avisar("Orquestador terminado", texto)


if __name__ == "__main__":
    main()
