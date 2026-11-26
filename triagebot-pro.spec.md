# TriageBot Pro

## Contexto

**Producto.** TriageBot Pro, aplicación web interna para que un equipo de soporte reciba tickets, los clasifique con IA, los asigne, los resuelva dentro de plazo y mida su rendimiento. Debe parecer un producto profesional terminado, no una demo.

**Stack obligatorio.** Python 3.12, FastAPI, Pydantic v2, sqlite3 de la librería estándar (sin ORM: nada de SQLModel ni SQLAlchemy), plantillas Jinja2 + HTMX 2 (cargado en la plantilla base desde https://unpkg.com/htmx.org@2.0.4), CSS propio sin frameworks en app/static/, pytest y ruff. Sesiones con SessionMiddleware de Starlette (itsdangerous). LLM: OpenRouter con el SDK openai (base_url https://openrouter.ai/api/v1, modelo openai/gpt-oss-120b, clave en la variable de entorno OPENROUTER_API_KEY).

**Estructura.** paquete app/ con main.py (create_app()), config.py, db.py, auth.py, cli.py, routers/, services/, templates/ y static/; tests/ con al menos un fichero de test por módulo y tests/fixtures/ para ficheros de ejemplo.

**Reglas.** dependencias fijadas con == en requirements.txt e instaladas en .venv; configuración solo por variables de entorno, con .env.example y sin secretos en el repo; los tests nunca usan la red: el cliente LLM se inyecta como dependencia de FastAPI y en los tests se sustituye por un falso; cada test usa una base de datos temporal; la hora actual se obtiene de un reloj inyectable para poder testear plazos; textos de la interfaz en español; código y nombres en español o inglés, pero coherentes en todo el proyecto. Antes de terminar cada funcionalidad, ejecuta ruff y pytest y deja ambos en verde.

## Verificación

- `.venv/bin/ruff check .`
- `.venv/bin/pytest -q`

## Comandos permitidos

- `Bash(.venv/bin/pytest:*)`
- `Bash(.venv/bin/ruff:*)`
- `Bash(.venv/bin/pip install:*)`
- `Bash(.venv/bin/pip freeze:*)`
- `Bash(.venv/bin/python:*)`
- `Bash(mkdir:*)`

## F01 · Esqueleto del proyecto

Base sobre la que se construye todo lo demás: aplicación FastAPI vacía pero profesional, configuración, calidad de código y documentación de arranque.

### Criterios

- app/main.py expone create_app() y app; `.venv/bin/python -m uvicorn app.main:app` arranca sin errores
- GET /health devuelve 200 con {"status": "ok"}
- requirements.txt contiene todas las dependencias fijadas con == (fastapi, uvicorn, jinja2, python-multipart, itsdangerous, openai, pytest, httpx, ruff)
- pyproject.toml configura ruff y pytest; `ruff check .` y `pytest -q` pasan
- .env.example lista SECRET_KEY, DATABASE_PATH y OPENROUTER_API_KEY con comentarios; app/config.py las lee y falla con un mensaje claro si falta SECRET_KEY
- README.md explica cómo instalar, configurar y arrancar
- Hay un test para /health

## F02 · Base de datos y migraciones

Capa de datos con sqlite3 y un sistema de migraciones sencillo y versionado.

### Criterios

- app/db.py abre conexiones a DATABASE_PATH con row_factory y PRAGMA foreign_keys = ON
- Existe una tabla schema_version y migraciones numeradas en código que se aplican una sola vez y en orden al arrancar la app
- La migración inicial crea las tablas usuarios, tickets, comentarios y auditoria con claves foráneas e índices en las columnas por las que se filtra
- Arrancar la app dos veces seguidas no duplica tablas ni datos ni lanza errores
- Los tests usan un fichero de base de datos temporal distinto por test mediante una fixture
- Hay tests de la aplicación idempotente de migraciones y de las claves foráneas

## F03 · Usuarios y contraseñas

Modelo de usuario y almacenamiento seguro de contraseñas, más un comando para crear el primer administrador.

### Criterios

- Un usuario tiene id, email único (normalizado a minúsculas), nombre, rol (admin o agente), activo, hash de contraseña y fecha de creación
- Las contraseñas se guardan con hashlib.scrypt y sal aleatoria por usuario; nunca se guarda ni se registra la contraseña en claro
- La comparación de hashes usa hmac.compare_digest
- Contraseñas de menos de 10 caracteres se rechazan con un mensaje claro
- `.venv/bin/python -m app.cli crear-admin --email X --nombre Y` pide la contraseña sin mostrarla y crea un administrador
- Hay tests de contraseña correcta, incorrecta, demasiado corta y email duplicado

## F04 · Login, logout y sesiones

Acceso con email y contraseña. Nada de la aplicación es visible sin iniciar sesión.

### Criterios

- GET /login muestra un formulario; con credenciales correctas se crea la sesión y se redirige con 303 a la bandeja
- Con credenciales incorrectas se muestra el mismo mensaje genérico tanto si el email no existe como si la contraseña es errónea
- Todas las rutas salvo /login, /health y /static exigen sesión: las páginas redirigen a /login y las rutas /api devuelven 401
- POST /logout cierra la sesión y redirige a /login
- La cookie de sesión está firmada con SECRET_KEY y es HttpOnly y SameSite=Lax
- Tras 5 intentos fallidos seguidos, ese usuario queda bloqueado 15 minutos aunque luego acierte la contraseña
- Hay tests para cada uno de estos casos

## F05 · Roles y permisos

Dos roles: admin gestiona usuarios y ve la auditoría; agente trabaja tickets.

### Criterios

- Existe una dependencia reutilizable require_role(...) para proteger rutas
- Las rutas bajo /admin devuelven 403 a un agente y 200 a un admin
- Un usuario desactivado no puede iniciar sesión y, si tenía una sesión abierta, su siguiente petición lo expulsa a /login
- Hay tests de acceso permitido y denegado para cada rol y para usuario desactivado

## F06 · API de tickets

API REST completa de tickets, base de la interfaz.

### Criterios

- POST /api/tickets con asunto, texto y email_cliente crea un ticket en estado nuevo y devuelve 201 con el ticket
- GET /api/tickets pagina (page, page_size hasta 100) y filtra por estado, categoria, prioridad, asignado_a y q (búsqueda en asunto y texto)
- GET /api/tickets/{id} devuelve el ticket o 404
- PATCH /api/tickets/{id} permite cambiar estado, prioridad, categoria y asignado_a, validando con Pydantic (422 si el valor no es válido)
- Los modelos de entrada y salida están en Pydantic y aparecen documentados en /docs
- Hay tests de cada endpoint, de cada filtro y de la paginación

## F07 · Clasificación automática con IA

Cada ticket nuevo se clasifica con el LLM. Si la IA falla, el sistema sigue funcionando.

### Criterios

- Al crear un ticket se obtiene del LLM una categoría (facturacion, tecnico, cuenta, envio u otro), una prioridad (alta, media o baja) y una justificación de una frase
- El cliente vive en app/services/clasificador.py, usa el SDK openai contra OpenRouter con timeout de 20 segundos y pide la respuesta en JSON
- La respuesta se valida con un modelo Pydantic; si hay error, timeout o JSON inválido, el ticket se crea igualmente con categoría sin_clasificar y el error queda registrado en el log
- POST /api/tickets/{id}/reclasificar vuelve a clasificar un ticket
- El cliente se inyecta como dependencia de FastAPI y los tests lo sustituyen por un falso
- Hay tests con el cliente falso para respuesta válida, JSON inválido y excepción

## F08 · Bandeja de tickets

Pantalla principal del agente: ver, filtrar y buscar tickets con rapidez.

### Criterios

- La página / muestra una tabla con asunto, cliente, categoría, prioridad, estado, asignado y antigüedad legible ("hace 3 h")
- Los filtros (estado, categoría, prioridad, asignado, solo míos) y el buscador actualizan solo la tabla mediante HTMX, sin recargar la página
- Paginación de 25 en 25, también con HTMX
- Prioridad y estado se muestran como etiquetas de color que siempre incluyen el texto
- Cada fila enlaza al detalle del ticket
- Hay tests de la página completa y del fragmento HTMX con filtros aplicados

## F09 · Detalle y ciclo de vida del ticket

Pantalla de un ticket donde se trabaja: estado, asignación y correcciones de la IA.

### Criterios

- /tickets/{id} muestra asunto, texto, cliente, fechas, clasificación con su justificación y persona asignada
- Estados: nuevo, en_curso, esperando_cliente, resuelto y cerrado, con una tabla de transiciones permitidas definida en un solo sitio; una transición no permitida devuelve error y no cambia nada
- Se puede asignar a cualquier agente activo y hay un botón "Asignarme"
- Cambiar a mano categoría o prioridad marca el ticket como corregido por una persona
- Las acciones se hacen con HTMX y actualizan solo la parte afectada de la página
- Hay tests de todas las transiciones válidas y de al menos tres inválidas

## F10 · Comentarios internos y línea de tiempo

Notas internas del equipo sobre cada ticket.

### Criterios

- Desde el detalle se añaden comentarios con HTMX; cada uno guarda autor y fecha
- El detalle muestra una línea de tiempo ordenada que mezcla comentarios y cambios del ticket
- Un comentario vacío o de más de 5000 caracteres se rechaza con mensaje junto al campo
- El contenido de los comentarios se escapa al mostrarse (no se ejecuta HTML)
- Hay tests de alta, validación y escapado

## F11 · SLA y vencimientos

Cada ticket tiene un plazo según su prioridad y se ve de un vistazo cuáles se han pasado.

### Criterios

- Al crear un ticket se calcula su fecha límite: alta 4 horas, media 24 horas, baja 72 horas; se recalcula si cambia la prioridad
- Un ticket está vencido si no está resuelto ni cerrado y ha pasado su fecha límite
- La bandeja y el detalle muestran la etiqueta "Vencido" y el tiempo restante o excedido
- La bandeja tiene un filtro "Solo vencidos"
- Los cálculos usan el reloj inyectable y se testean sin depender de la hora real
- Hay tests de cálculo de plazo, recálculo y vencimiento

## F12 · Auditoría

Registro inmutable de quién hizo qué y cuándo.

### Criterios

- Crear ticket, cambiar estado, prioridad, categoría o asignación, reclasificar, iniciar sesión (con éxito o no) y gestionar usuarios generan un registro en auditoria con usuario, fecha, acción, valor anterior y valor nuevo
- Los cambios del ticket aparecen en su línea de tiempo
- /admin/auditoria, solo para admin, lista los registros con filtro por usuario, acción y rango de fechas
- No existe ninguna ruta que modifique o borre registros de auditoría
- Hay tests que comprueban el registro generado por cada tipo de acción

## F13 · Panel de métricas

Visión de dirección: cómo va el soporte hoy.

### Criterios

- /panel muestra totales por estado, categoría y prioridad, número de vencidos, tiempo medio de resolución en horas y tickets resueltos por agente en los últimos 7 días
- Los gráficos son barras SVG generadas en el servidor, sin librerías JavaScript, con el valor escrito junto a cada barra
- Las métricas se calculan en una función de servicio separada de la vista
- Hay tests que cargan datos conocidos y comprueban cada cifra

## F14 · Importación y exportación

Cargar tickets existentes y sacar datos para Excel.

### Criterios

- `.venv/bin/python -m app.cli importar RUTA` carga tickets desde CSV (columna obligatoria texto; opcionales asunto y email_cliente) o desde JSON (lista de objetos con texto) y los clasifica
- La opción --sin-clasificar importa sin llamar al LLM y deja la categoría sin_clasificar
- Filas sin texto se saltan y el comando informa al final de importados y saltados
- GET /api/tickets/export.csv exporta con los mismos filtros que la bandeja, y la bandeja tiene un botón Exportar que respeta los filtros activos
- Hay tests con ficheros pequeños en tests/fixtures para CSV, JSON y exportación

## F15 · Gestión de usuarios y mi cuenta

El admin gestiona el equipo sin tocar la base de datos.

### Criterios

- /admin/usuarios permite listar, crear, cambiar rol, desactivar y reactivar usuarios
- Restablecer contraseña genera una temporal que se muestra una sola vez y obliga a cambiarla en el siguiente inicio de sesión
- Un admin no puede desactivarse a sí mismo ni quitar el rol al último admin activo
- /mi-cuenta permite a cualquier usuario cambiar su contraseña indicando la actual
- Hay tests de cada acción y de las dos protecciones

## F16 · Seguridad

Endurecimiento propio de una aplicación que se va a exponer en una empresa.

### Criterios

- Todos los formularios POST y las peticiones HTMX que modifican datos llevan un token CSRF ligado a la sesión; sin token válido se responde 403
- Todas las respuestas incluyen Content-Security-Policy, X-Content-Type-Options: nosniff, X-Frame-Options: DENY y Referrer-Policy
- Los errores 404 y 500 muestran páginas propias sin trazas ni detalles internos
- Ninguna consulta SQL se construye concatenando texto del usuario: todas usan parámetros
- Hay tests de CSRF, cabeceras y páginas de error

## F17 · Acabado profesional y datos de demostración

Lo que hace que alguien que abre la app piense que es un producto real.

### Criterios

- Una plantilla base común con barra superior ("TriageBot Pro"), navegación según el rol, nombre del usuario conectado y botón de cerrar sesión
- Mensajes flash de confirmación o error tras cada acción
- Formularios con label en cada campo, errores junto al campo y foco visible con teclado
- Diseño adaptable: la bandeja y el detalle se usan bien a 375 px de ancho
- `.venv/bin/python -m app.cli demo` crea un admin, 3 agentes y 60 tickets variados en estados, prioridades y fechas, sin llamar al LLM, e imprime las credenciales de demostración
- Hay tests que comprueban que cada página principal responde 200 con sesión iniciada y contiene la navegación

## F18 · Integración continua y documentación final

Cierre del producto: calidad automática en GitHub y documentación para quien llegue después.

### Criterios

- .github/workflows/ci.yml ejecuta ruff y pytest con Python 3.12 en cada push y pull request, sin necesitar secretos
- README.md describe el producto, sus funcionalidades, cómo arrancar la demo en tres comandos y la estructura del código
- README.md incluye la insignia del estado del workflow
- Todos los tests pasan y ruff no da avisos
