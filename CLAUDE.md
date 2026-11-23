# CLAUDE.md - Alter Tracker Backend

Guía de prácticas de código para este repositorio. Refleja patrones ya establecidos - seguirlos mantiene el código coherente.

---

## Proyecto

API FastAPI en Python 3.13 para gestionar el control de puerta de eventos comunitarios. Sincroniza RSVPs desde Mazmo, registra check-ins, mantiene un audit trail completo, y gestiona bans de guests.

**Stack**: FastAPI · PostgreSQL 18 · SQLModel (Pydantic + SQLAlchemy) · Alembic · structlog · Argon2 · JWT (HS256) · httpx

---

## Arquitectura (capas)

```
routers -> schemas -> services -> models -> database
```

| Capa | Qué hace | No hace |
|------|----------|---------|
| `app/routers/` | HTTP handling, auth deps, error conversion, logging de acciones | Lógica de negocio compleja |
| `app/schemas/` | Validación Pydantic v2, shapes de request/response | Tocar DB |
| `app/services/` | Lógica de negocio pura, llamadas externas (Mazmo) | Imports de FastAPI, HTTPException |
| `app/models/` | SQLModel tables, relaciones, enums de dominio | Validación HTTP |
| `app/core/deps.py` | Cadena de auth via dependency injection | - |

---

## Configuración (`app/core/config.py`)

- `Settings(BaseSettings)` de pydantic-settings. Todo viene de env vars o `.env`.
- Campos requeridos sin default: `database_url`, `jwt_signing_key`. Pydantic falla en startup si faltan.
- Singleton cacheado con `@lru_cache` en `get_settings()`.
  - En **routers**: inyectar como `settings: Settings = Depends(get_settings)` para que sea overrideable en tests.
  - En **inicialización de módulo** (engine, security): llamar directamente `get_settings()`.

```python
# Correcto en routers
async def my_route(settings: Settings = Depends(get_settings)): ...

# Correcto en módulo (database.py, security.py)
settings = get_settings()
engine = create_engine(settings.database_url, ...)
```

---

## Logging (`app/core/logging.py`)

Usar `structlog`. El request ID se inyecta automáticamente en todos los logs del request via `RequestContextMiddleware`.

```python
import structlog
log = structlog.get_logger(__name__)  # siempre con __name__, al inicio del módulo

# Log con contexto (keyword args -> campos JSON en producción)
log.info("Check-in recorded", guest_id=123, meetup_id="abc-uuid", staff="carlos")

# Bind contexto persistente para el scope
log = log.bind(meetup_id=str(meetup_id))
log.info("Sync started")   # incluye meetup_id automáticamente
log.info("Sync complete")  # incluye meetup_id automáticamente
```

**Regla**: logear **después** de que la acción ocurre (post-commit), no antes.

- Dev: pretty console con colores (`json_logs=False`)
- Producción: JSON para CloudWatch (`json_logs=True`)
- Silenciados: `httpx`, `httpcore`, `sqlalchemy.engine`

---

## Patrones de routers

### Estructura de archivo

```python
"""
Nombre del router

POST /resource/  -> descripción
GET  /resource/  -> descripción
"""
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
...

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/resource", tags=["resource"])


def _get_thing_or_404(session: Session, thing_id: ...) -> Thing:
    """Helpers privados reutilizables dentro del archivo."""
    thing = session.get(Thing, thing_id)
    if not thing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=(...))
    return thing


@router.post(
    "/",
    response_model=ThingPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create a thing",          # siempre incluir summary
    responses=CREATE_THING_RESPONSES,  # OpenAPI examples
)
async def create_thing(                # siempre async def
    payload: ThingCreate,
    session: Session = Depends(get_session),
    _staff: User = Depends(get_approved_user),  # _ cuando no se usa el objeto
) -> Thing:
    ...
```

### Auth dependencies

```python
# Solo verificar auth, sin usar el objeto
_staff: User = Depends(get_approved_user)
_admin: User = Depends(get_admin_user)

# Cuando necesitás el usuario (para audit log, etc.)
staff: User = Depends(get_approved_user)
admin: User = Depends(get_admin_user)
```

### HTTP status codes

| Situación | Status |
|-----------|--------|
| Create exitoso | 201 |
| Duplicado, estado inválido | 409 |
| Not found | 404 |
| Sin permisos | 403 |
| Mazmo retornó error | 502 |
| Mazmo no responde | 504 |
| Error interno (nunca debería pasar) | 500 |

### Mensajes de error

Todos los `HTTPException` detail siguen este patrón:

```python
raise HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail=(
        f"Cannot check in: guest '{guest.username}' (mazmo_user_id={mazmo_user_id}) "
        f"is already checked in. They arrived at {rsvp.arrival_time} "
        f"(arrival #{rsvp.arrival_order}). "
        f"To undo this, use PATCH /meetups/{meetup_id}/guests/{mazmo_user_id}/undo-checkin."
    ),
)
```

1. `"Cannot X:"` - qué falló
2. Contexto y valores relevantes (IDs, nombres, timestamps)
3. Qué hacer para resolverlo (`Try...`, `Use...`, `List ... to find...`)

### Commits y sesión

```python
session.add(thing)
session.commit()
session.refresh(thing)  # para obtener campos generados por DB (id, timestamps, trigger)
return thing
```

### Concurrencia

Usar `.with_for_update()` cuando la operación posterior depende del estado leído:

```python
rsvp = session.exec(
    select(MeetupRsvp)
    .where(...)
    .with_for_update()  # row lock hasta el commit
).first()
```

Capturar `IntegrityError` post-commit para race conditions:

```python
try:
    session.commit()
except IntegrityError:
    session.rollback()
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=(...)) from None
```

### Audit trail

Todo CHECK_IN, BAN, UNBAN, etc. crea un `EventLog` **en el mismo commit**:

```python
event = EventLog(
    event_type=EventType.CHECK_IN,
    actor_id=staff.id,
    guest_id=mazmo_user_id,
    meetup_id=meetup_id,
)
session.add(rsvp)
session.add(event)
session.commit()  # atómico: o ambos o ninguno
```

---

## Modelos SQLModel (`app/models/models.py`)

```python
class Thing(SQLModel, table=True):
    __tablename__ = "things"  # siempre explícito

    # PK autoincrement
    id: int | None = Field(default=None, primary_key=True)

    # PK UUID
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)

    # Campos indexados (para filtros y ordenamiento frecuente)
    name: str = Field(index=True)
    is_active: bool = Field(default=False, index=True)

    # Timestamps con timezone
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

### Enums

`StrEnum` para valores que viven como strings en DB - no son tipos Postgres nativos:

```python
class PossibleRoles(StrEnum):
    STAFF = "STAFF"
    ADMIN = "ADMIN"

class EventType(StrEnum):
    CHECK_IN = "CHECK_IN"
    BAN = "BAN"
    ...
```

### Domain types

`NewType` para distinguir IDs externos de internos:

```python
# app/domain_types/types.py
MazmoUserId = NewType("MazmoUserId", int)

# En modelo SQLModel (necesita sa_type explícito)
guest_id: MazmoUserId = Field(foreign_key="guests.mazmo_user_id", sa_type=Integer)

# Al construir
rsvp = MeetupRsvp(guest_id=MazmoUserId(some_int), ...)
```

### Soft deletes

Nunca `DELETE` usuarios. Usar flags:

```python
is_disabled: bool = Field(default=False)
disabled_at: datetime | None = Field(default=None)
disabled_by_id: int | None = Field(default=None, foreign_key="users.id")
disabled_reason: str | None = Field(default=None, max_length=500)
```

### Queries

```python
# Lookup por PK
session.get(Model, pk_value)

# Query con filtros
session.exec(select(Model).where(Model.field == value)).first()
session.exec(select(Model).where(Model.field == value)).all()

# Eager loading de relaciones
session.exec(
    select(Model)
    .where(...)
    .options(selectinload(Model.relationship))
).all()
```

---

## Schemas Pydantic v2 (`app/schemas/`)

```python
from pydantic import ConfigDict
from sqlmodel import SQLModel

class ThingPublic(SQLModel):
    model_config = ConfigDict(from_attributes=True)  # siempre en response schemas

    id: int
    name: str

# Conversión ORM -> schema
return ThingPublic.model_validate(orm_thing)
```

- Un archivo por dominio (`guests.py`, `meetups.py`, etc.)
- Re-exportar todo desde `app/schemas/__init__.py`
- Naming: `XxxPublic` (response), `XxxCreate` / `XxxRequest` (request)
- Nunca usar ORM models directamente como `response_model`

---

## Type checking

- `basedpyright` strict - correr antes de commit: `basedpyright`
- Sintaxis Python 3.12+: `X | Y` en lugar de `Optional[X]` / `Union[X, Y]`
- `# type: ignore[specific-error-code]` solo para falsos positivos conocidos, nunca `# type: ignore`
- `TypedDict` para dicts con shape definida: `class JWTPayload(TypedDict): sub: str; role: str`
- `typing.Annotated` para combinar tipos con FastAPI `Field()`: `Annotated[int, Field(gt=0)]`

---

## Servicios (`app/services/`)

- Sin imports de `fastapi`, sin `Depends`, sin `HTTPException`
- Errores propios que el router convierte:
  - `MazmoNetworkError` -> 504
  - `MazmoAPIError` -> 502
- Reciben `session: Session`, `settings: Settings` como argumentos (no vía DI)
- `async` cuando hacen I/O (httpx, etc.)

```python
# services/mazmo.py
class MazmoNetworkError(Exception): ...
class MazmoAPIError(Exception): ...

class MazmoClient:
    async def __aenter__(self): ...
    async def __aexit__(self, ...): ...

# En el router
try:
    async with MazmoClient(settings) as client:
        result = await client.do_thing()
except MazmoNetworkError as exc:
    raise HTTPException(status_code=504, detail=f"... {exc}") from exc
except MazmoAPIError as exc:
    raise HTTPException(status_code=502, detail=f"... {exc}") from exc
```

---

## OpenAPI examples (`app/openapi_examples/`)

Cada router tiene su archivo correspondiente con ejemplos de request y response para Swagger.

```python
# openapi_examples/meetups_examples.py
CREATE_MEETUP_REQUEST_EXAMPLES = {
    "example_name": {"summary": "...", "value": {...}},
}
CREATE_MEETUP_RESPONSES = {
    409: {"description": "...", "content": {...}},
}

# En el router
@router.post(
    "/",
    responses=CREATE_MEETUP_RESPONSES,
)
async def create_meetup(
    payload: Annotated[MeetupCreate, Body(openapi_examples=CREATE_MEETUP_REQUEST_EXAMPLES)],
    ...
```

---

## Tests (`tests/`)

### Infraestructura

```
setup_test_database (session-scoped)
  -> Crea DB test, corre migrations, seed roles, crea trigger set_arrival_order
  -> Corre UNA VEZ por sesión de tests

session (function-scoped)
  -> Wraps cada test en transacción
  -> Rollback automático al final -> tests aislados sin cleanup manual

client (function-scoped)
  -> TestClient con get_session overrideado para compartir la transacción del test
  -> La app y el test ven los mismos datos
```

**Nunca mockear la DB**. Se usa PostgreSQL real (base de datos de test).

### Helpers de conftest

```python
from tests.conftest import make_user, make_guest, make_meetup, make_rsvp

# Usan session.flush() (no commit) para obtener IDs sin salir de la transacción
guest = make_guest(session, mazmo_user_id=1, username="alice")
meetup = make_meetup(session, name="Alter #5")
rsvp = make_rsvp(session, meetup=meetup, guest=guest)
```

Fixtures de auth listas para usar: `admin_headers`, `staff_headers`, `pending_user`.

### Naming de tests

```
test_<acción>_<condición>_<resultado_esperado>

test_create_meetup_returns_201_with_meetup_data
test_create_meetup_returns_409_for_duplicate_url
test_checkin_returns_404_when_guest_not_rsvped
```

### Estructura de test

```python
def test_checkin_returns_409_when_already_arrived(
    client: TestClient, staff_headers: dict, session: Session
):
    """
    Verify that checking in an already-arrived guest returns 409.

    WHY: Prevents double check-ins from duplicate button presses.
    """
    # Arrange
    guest = make_guest(session, mazmo_user_id=1)
    meetup = make_meetup(session)
    make_rsvp(session, meetup=meetup, guest=guest, has_arrived=True)

    # Act
    resp = client.post(
        f"/meetups/{meetup.id}/guests/1/checkin",
        headers=staff_headers,
    )

    # Assert
    assert resp.status_code == status.HTTP_409_CONFLICT
```

### Mocking de Mazmo

Parchear en el **punto de importación**, no en la definición:

```python
# Para tests de sync
@pytest.fixture
def mock_mazmo():
    with patch("app.services.sync.MazmoClient") as MockClass:
        mock = AsyncMock()
        mock.__aenter__.return_value = mock
        mock.__aexit__.return_value = None
        MockClass.return_value = mock
        yield mock

# Para tests de guests router
with patch("app.routers.guests.MazmoClient") as MockClass: ...
```

---

## Migrations (Alembic)

- Archivos en `alembic/versions/`, nombrados `NNNN_descripcion_corta.py`
- Siempre incluir `down_revision` correcta para reversibilidad
- Data migrations (backfills) van en la misma migration que el schema change cuando son pequeñas
- Ejecutar: `alembic upgrade head`
- Nueva migration: `alembic revision --autogenerate -m "descripcion"`

---

## Comandos de desarrollo (devenv)

```bash
dev-backend    # Levanta el servidor FastAPI (uvicorn)
db-start       # Inicia PostgreSQL en Docker
db-migrate     # Corre alembic upgrade head
db-revision    # Genera nueva migration autogenerate
seed-admin     # Crea usuario admin inicial
run-tests      # pytest
coverage       # pytest con reporte de cobertura
lint           # ruff check
format         # ruff format
```

---

## Produccion: la instancia EC2

El README tiene la guia para humanos (sesion interactiva, primer deploy, acceso a la DB). Esta seccion es para agentes: todo se hace con comandos no interactivos.

### Datos fijos

| Que | Valor |
|-----|-------|
| Instancia | `i-054c0dd0cd6c37dc5`, `us-east-1`, `t4g.small` (arm64) |
| Estado normal | **Siempre prendida.** Los workflows `start-instance.yml` / `stop-instance.yml` existen pero no se usan en el dia a dia. |
| API publica | `https://api-alter-tracker.club-vanta.com` (Cloudflare proxied, SSL flexible -> la instancia escucha en `:80`) |
| Archivos en el server | `/home/ec2-user/deploy.sh`, `/home/ec2-user/docker-compose.yml` |
| Contenedores | `ec2-user-app-1`, `ec2-user-db-1`, `ec2-user-weather-worker-1`, `ec2-user-backup-1` |
| Secrets | Secrets Manager `alter-tracker-backend/secrets`, los lee `deploy.sh` con el rol de la instancia. Nunca leerlos desde afuera. |
| Logs | CloudWatch, grupo `/alter-tracker-backend`, un stream por servicio (`app`, `db`, `migrate`, `seed`) |
| Organizaciones | Club Vanta tiene slug `club-vanta`. La base es multi-organizacion: hay otras orgs que no se exponen en nada publico. |

### Credenciales

Hace falta un perfil de AWS con permisos de SSM sobre la instancia. En la maquina del mantenedor es el perfil `base` de `~/.aws/credentials`: exportar `AWS_PROFILE=base` (no hay perfil `default`). Verificar con:

```bash
aws ssm describe-instance-information --region us-east-1 \
  --query 'InstanceInformationList[].{id:InstanceId,ping:PingStatus}' --output text
# i-054c0dd0cd6c37dc5   Online
```

### Correr un comando en la instancia (no interactivo)

`aws ssm start-session` necesita una terminal y el plugin de Session Manager: no sirve para agentes. Usar `send-command` y leer el resultado. Los comandos corren como `root`.

```bash
CID=$(aws ssm send-command --region us-east-1 \
  --instance-ids i-054c0dd0cd6c37dc5 \
  --document-name AWS-RunShellScript \
  --comment "que hace este comando" \
  --parameters 'commands=["docker ps --format {{.Names}}"]' \
  --query Command.CommandId --output text)
sleep 5
aws ssm get-command-invocation --region us-east-1 \
  --command-id "$CID" --instance-id i-054c0dd0cd6c37dc5 \
  --query '[Status,StandardOutputContent,StandardErrorContent]' --output text
```

Si `Status` es `InProgress`, esperar y volver a pedir la invocacion. Para comandos con comillas, el JSON de `commands` se vuelve dificil de escapar: escribir el script en un archivo y pasarlo con `--parameters file://params.json`.

### Consultas a la base (solo lectura)

El contenedor `db` tiene `POSTGRES_USER` en su entorno, asi que no hace falta tocar los secrets:

```bash
docker exec ec2-user-db-1 sh -c \
  'psql -U "$POSTGRES_USER" -d alter_event_tracker -At -c "select name, slug from organizations"'
```

Nunca correr `UPDATE`, `DELETE` ni DDL a mano: los cambios de datos van por migrations o scripts de `scripts/` con tests.

### Deploy

1. Push a `main` -> CD (`cd.yml`) construye y publica `ghcr.io/club-vanta/alter-tracker-backend:latest` (arm64). **No deploya solo.**
2. En la instancia: `bash /home/ec2-user/deploy.sh`. Trae los secrets, hace `docker compose pull` y `up -d`; el servicio `migrate` corre `alembic upgrade head` antes de que arranque `app`.
3. Verificar: `curl -s https://api-alter-tracker.club-vanta.com/health`.

Antes de correr `deploy.sh`, confirmar que el job de CD de ese commit termino (`gh run list --workflow cd.yml --limit 1`): si no, se baja la imagen anterior.

### Cambios a `docker-compose.yml` o `deploy.sh`

Los dos archivos se copiaron al server una sola vez, en el `user_data` de la instancia, y `infra/ec2.tf` ignora cambios a `user_data` (`lifecycle.ignore_changes`). O sea: **editar el archivo en el repo no cambia el server**, y `tofu apply` no reemplaza la instancia por eso. Para llevar un cambio:

1. Backup en el server: `cp /home/ec2-user/docker-compose.yml /home/ec2-user/docker-compose.yml.bak-$(date +%F)`.
2. Comparar el archivo del server con el del repo en `main` (`cat` por SSM y `diff` local). Si difieren en algo que no es el cambio nuevo, frenar y averiguar por que antes de pisarlo.
3. Aplicar el mismo cambio en el server y correr `deploy.sh`.

### Correr un script de `scripts/` en produccion

Dentro del contenedor `app`, que ya tiene `DATABASE_URL`:

```bash
docker exec -e PYTHONPATH=/app ec2-user-app-1 python scripts/<script>.py
```

---

## Reglas importantes

1. **Soft delete siempre**: nunca `DELETE` de `users`, usar `is_disabled`.
2. **Sync idempotente**: el upsert NUNCA sobrescribe `has_arrived`, `arrival_time`, `arrival_order`, `checked_in_by_id`, ni `guest_type`.
3. **Audit trail atómico**: EventLog en el mismo commit que el cambio de estado.
4. **Row locking en check-in**: `.with_for_update()` para prevenir check-ins duplicados concurrentes.
5. **Domain types**: `MazmoUserId` para IDs de Mazmo, `int` para IDs internos.
6. **Services sin FastAPI**: los servicios no deben importar nada de `fastapi`.
7. **Tests con real DB**: nunca mockear la sesión/DB en tests.
8. **Error messages accionables**: siempre decir qué falló, por qué, y qué hacer.
9. **Solo ASCII en strings, comentarios y documentacion**: usar solo caracteres ASCII basicos. No usar em-dashes, flechas Unicode, ni otros caracteres especiales no-ASCII. Usar `-` en lugar de em-dash, `->` en lugar de flecha Unicode, etc.
10. **Calidad de PR senior**: todo cambio tiene que estar bien configurado, ser eficiente y modular, y aprobar el review de un senior. "Funciona" no alcanza:
    - Seguir las capas y los patrones de este documento. Sin codigo duplicado, codigo muerto ni logs de debug.
    - Logica nueva con tests, y bug fixes con un test que falle sin el fix.
    - Antes de dar algo por terminado tienen que pasar `basedpyright`, `lint` y `run-tests`.
    - En cambios no triviales, correr un agente de review (por ejemplo `/code-review`) sobre el diff antes de abrir el PR. Revisa correctitud, calidad del codigo y arquitectura. Corregir los hallazgos reales, rebatir los incorrectos con una razon, y recien ahi abrir el PR.

---

## Lecciones de reviews anteriores

Cada punto fue un hallazgo real en un PR anterior. Revisarlos antes de pedir review.

- **Reglas de negocio en services, no en routers.** Conteos y clasificaciones van en el service (ejemplo: `summarize_attendance` en `app/services/attendance.py`). El router solo mapea el resultado al schema de respuesta. Si la regla esta en el router, la proxima feature la copia.
- **Response schemas sin defaults en campos que siempre se completan.** Un default (`= None`, `= "NORMAL"`) hace que OpenAPI marque el campo como opcional y el frontend recibe tipos opcionales. Usar el enum del dominio (`GuestType`), no `str`.
- **Combinar respuestas OpenAPI con `merge_error_responses`**, nunca con `**`. Dos errores con el mismo status (dos 404) se pisan y uno desaparece del `openapi.json`.
- **Indices para patrones de query nuevos.** Si una query filtra por una columna que no es la primera de la PK o de un indice (ejemplo: `meetup_rsvps.guest_id`), agregar `index=True` y la migration.
- **Cada fila de la tabla de reglas del spec tiene su test.** El caso walk-in sin check-in quedo sin test y aparecio como NO_SHOW incorrecto en el review.
- **Tests de cada ruta nueva**: incluir `test_..._returns_401_without_auth`, y verificar valores concretos (timestamps, conteos), no solo `is not None`.
- **Separadores de seccion en ASCII**: `# -- Seccion --`, no caracteres de dibujo de cajas.
