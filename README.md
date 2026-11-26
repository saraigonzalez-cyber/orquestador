# Orquestador de Claude Code

Recorre las funcionalidades de una spec escrita en Markdown, una a una. Para cada una:

1. **Implementador** (`claude -p`, puede editar ficheros y ejecutar solo los comandos permitidos) programa la funcionalidad y sus tests.
2. **Verificación**: el script ejecuta los comandos de `## Verificación`. Si fallan, los errores vuelven al implementador (misma sesión, con `--resume`).
3. **Revisor**: una sesión nueva, de solo lectura, recibe el diff y los criterios y devuelve `{"aprobado": ..., "problemas": [...]}`.
4. Si se aprueba, el script hace `git commit` y pasa a la siguiente. Si no, los problemas vuelven al implementador.
5. Tras `MAX_ITERACIONES` intentos fallidos, la funcionalidad queda **bloqueada**: el trabajo se guarda como `.patch` en `logs/`, el repo vuelve al último commit y el script sigue con la siguiente.


## Descarga (alumnos)

```
git clone https://github.com/carlospologil/orquestador-kit.git lab10
cd lab10
mkdir triagebot-pro && cd triagebot-pro   # vuestro repo va dentro; el kit lo ignora
```

Si el kit cambia durante el lab: `git pull` dentro de `lab10`.

Para ver el consumo (tiempo, coste y tokens por llamada): `python3 ../consumo.py` desde vuestro repo. Escribe `consumo.csv` junto al kit (fuera de vuestro repo) y muestra un resumen por funcionalidad.

## Dos ficheros: la spec la escribe una persona, el estado lo escribe la máquina

| Fichero | Lo escribe | Contenido |
|---|---|---|
| `triagebot-pro.spec.md` | Una persona | Contexto, verificación, comandos permitidos y funcionalidades con sus criterios |
| `triagebot-pro.estado.json` | El orquestador | Estado de cada funcionalidad (`hecha`, `bloqueada`, `error`), iteraciones, fecha y último feedback |

Así la spec se lee, se revisa y se versiona como cualquier documento, y nunca se mezcla con datos que cambian en cada ejecución.

## Formato de la spec

```markdown
# Nombre del proyecto

## Contexto
Texto libre: producto, stack, estructura y reglas. Viaja en cada prompt.

## Verificación
- `.venv/bin/ruff check .`
- `.venv/bin/pytest -q`

## Comandos permitidos
- `Bash(.venv/bin/pytest:*)`

## F01 · Título de la funcionalidad
Descripción libre.

### Criterios
- Un criterio que un test pueda comprobar
- Otro criterio (puede seguir en la línea siguiente,
  con sangría)
```

Reglas: cada funcionalidad es un `## F01 · Título` (vale también `:` o `-`), y sus criterios van en una lista bajo `### Criterios`. `--dry-run` comprueba la spec y, si algo falla, dice en qué línea.

## Puesta en marcha

```bash
mkdir triagebot-pro && cd triagebot-pro && git init
printf ".venv/\n__pycache__/\n*.db\n.env\n" > .gitignore
python3 -m venv .venv && .venv/bin/pip install pytest ruff
git add . && git commit -m "Inicio"
gh repo create triagebot-pro --public --source . --push
git checkout -b auto/pro

python3 ../orquestador.py --repo . --spec ../triagebot-pro.spec.md --dry-run   # comprueba la spec
python3 ../orquestador.py --repo . --spec ../triagebot-pro.spec.md            # lanza

git push -u origin auto/pro && gh pr create --fill --base main                # al terminar
```

El script y la spec van en la carpeta de arriba, fuera del repo. Para dejarlo corriendo aunque cierres la terminal: `tmux` o `nohup`.

## Qué tocar

- **La spec (`.md`)**: es lo que se programa. Si una funcionalidad se bloquea, casi siempre hay que concretar un criterio o partirla.
- **Cabecera de `orquestador.py`**: intentos, turnos, topes de gasto (por llamada y total), modelos (`MODELO_IMPL`, `MODELO_REV`) y los tres prompts. Para clase: `MAX_ITERACIONES = 3` y `MAX_USD_TOTAL = 8`.

## Relanzar y reintentar

- Si se corta (tope de gasto, límite de uso, cierre del portátil), **relanza el mismo comando**: solo procesa lo que siga pendiente en `.estado.json`.
- Para reintentar una funcionalidad bloqueada, después de mejorar su spec: `--solo F07`.
- Para empezar de cero, borra `triagebot-pro.estado.json`.

## Al volver

- `git log` muestra un commit por cada funcionalidad aprobada.
- `logs/<fecha>/orquestador.log` es el resumen; los `.json` guardan la salida completa de cada llamada a Claude.
- En las bloqueadas, `ultimo_feedback` (en `.estado.json`) y el `.patch` explican qué faltó.

## Seguridad

- No usa `--dangerously-skip-permissions`. El implementador edita ficheros (`acceptEdits`) y solo ejecuta los comandos de tu lista. El revisor solo puede leer.
- Para darle más autonomía (instalar paquetes del sistema, por ejemplo), hazlo dentro de un contenedor.

Aviso al móvil (opcional): `export NTFY_TOPIC=un-nombre-dificil-de-adivinar` y suscríbete a ese tema en la app ntfy.

## Modelos: OpenRouter

El orquestador lanza Claude Code contra OpenRouter: solo necesitas tu clave en el terminal.

```
export OPENROUTER_API_KEY="sk-or-..."     # Mac: añádelo a ~/.zshrc · WSL: a ~/.bashrc
```

El script se encarga del resto al llamar a `claude` (ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN y ANTHROPIC_API_KEY vacía); no toca tu configuración de Claude Code fuera de la ejecución.

- **Modelo**: `MODELO_IMPL = "opus" | "sonnet" | "haiku"` en la cabecera. `MODELOS_OPENROUTER` dice a qué modelo de OpenRouter va cada alias. `MODELO_REV = None` usa el mismo para revisar.
- **Gasto**: el coste de cada llamada se mide con el crédito de la clave en OpenRouter (y, si no se puede consultar, con la estimación de Claude Code). Al arrancar muestra el crédito restante.
- **Tope real**: crea la clave con un límite de crédito en OpenRouter (Settings › Keys). Si se agota, OpenRouter deja de responder y el orquestador se para sin marcar nada.
- **Sin OpenRouter**: `USAR_OPENROUTER = False` y usa la sesión o la clave que tenga tu Claude Code.

Al arrancar hace una llamada mínima de prueba. Si Claude no responde (clave mala, sin crédito…), se para con el mensaje de error y no toca el repo.
