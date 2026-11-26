# API de reservas

## Contexto

Python 3.12, FastAPI, SQLAlchemy, pytest. Código en src/, tests en tests/. Sigue el estilo existente (ruff). No añadas dependencias nuevas sin justificarlo.

## Verificación

- `pytest -q`
- `ruff check .`

## Comandos permitidos

- `Bash(pytest:*)`
- `Bash(ruff:*)`
- `Bash(python:*)`

## F1 · Crear reserva

Endpoint POST /reservas que crea una reserva para un recurso en una franja horaria.

### Criterios

- POST /reservas con {recurso_id, inicio, fin} devuelve 201 y la reserva creada con su id
- Si fin <= inicio devuelve 422 con mensaje claro
- Si la franja se solapa con otra reserva del mismo recurso devuelve 409
- Hay tests para los tres casos

## F2 · Cancelar reserva

Endpoint DELETE /reservas/{id} que cancela una reserva existente.

### Criterios

- DELETE de una reserva existente devuelve 204 y deja de aparecer en GET /reservas
- DELETE de un id inexistente devuelve 404
- Hay tests para ambos casos
