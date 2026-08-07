# Guests sin cuenta de Mazmo + campo de Instagram

**Date:** 2026-08-07
**Status:** Approved

## Goal

Hoy un guest no puede existir en el sistema sin tener una cuenta en Mazmo:
`guests.mazmo_user_id` es la primary key de la tabla, y es tambien el
identificador usado en toda la API (URLs de check-in, ban, walk-in, etc.) y
en las foreign keys de `meetup_rsvps`, `organization_bans` y `event_log`.

Los usuarios reportaron eventos con asistentes que no tienen perfil en
Mazmo. Hoy no hay forma de registrarlos: ni via sync, ni como walk-in, ni
manualmente.

Este cambio:

1. Desacopla la identidad interna de un guest de su cuenta de Mazmo, para
   que un guest pueda crearse sin `mazmo_user_id` y, mas adelante, vincular
   una cuenta de Mazmo real si aparece.
2. Agrega un campo opcional `instagram_username` a todos los guests.
3. Renombra `Guest.username` a `Guest.mazmo_handle`, porque una vez que
   Mazmo deja de ser obligatorio, "username" sugiere que todo guest tiene
   uno. El nombre nuevo deja explicito que es el handle de Mazmo
   especificamente, `None` cuando no aplica.

## Data model changes (`app/models/models.py`)

```python
class Guest(SQLModel, table=True):
    __tablename__ = "guests"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    mazmo_user_id: MazmoUserId | None = Field(default=None, unique=True, index=True, sa_type=Integer)
    mazmo_handle: str | None = Field(default=None, index=True)
    displayname: str
    instagram_username: str | None = Field(default=None, max_length=64)
```

- `id` (UUID) es la nueva PK. Sigue el mismo patron ya usado en
  `meetups.id` / `organizations.id` (UUID directo, sin `NewType` -
  `MazmoUserId` se reserva para el identificador externo).
- `mazmo_user_id` deja de ser PK: nullable, UNIQUE, indexado.
- `mazmo_handle` (antes `username`) pasa a nullable (no existe si no hay
  cuenta de Mazmo). Solo se usa para el handle de Mazmo; un guest manual
  no tiene equivalente local.
- `displayname` sigue obligatorio: es el nombre a mostrar, venga de Mazmo o
  lo cargue el staff a mano.
- `instagram_username` nuevo, opcional, sin integracion con la API de
  Instagram. Se normaliza sacando un `@` inicial si lo mandan, sin
  validacion de formato mas alla de `max_length`.

`MeetupRsvp.guest_id`, `OrganizationBan.guest_id` y `EventLog.guest_id`
cambian de `MazmoUserId` (int, FK a `guests.mazmo_user_id`) a `uuid.UUID`
(FK a `guests.id`).

Nuevo valor de `EventType`: `GUEST_MAZMO_LINKED` (ver seccion de linking).

## Migration (Alembic)

Patron "agregar columna nueva, backfillear, canjear FK, borrar vieja",
todo en una sola migracion:

1. `guests`: agregar `id uuid NOT NULL DEFAULT gen_random_uuid()`,
   backfillear filas existentes, dropear la PK de `mazmo_user_id`, crear
   la PK sobre `id`. Alterar `mazmo_user_id` a nullable + UNIQUE + index.
   Renombrar `username` a `mazmo_handle` y alterarla a nullable. Agregar
   `instagram_username varchar(64) NULL`.
2. En `meetup_rsvps`, `organization_bans`, `event_log`: agregar columna
   `guest_id_new uuid`, backfillear con
   `UPDATE ... FROM guests WHERE guest_id_new = guests.id AND guests.mazmo_user_id = <tabla>.guest_id`,
   dropear la FK y columna vieja, renombrar `guest_id_new` -> `guest_id`,
   crear la FK contra `guests.id` con los mismos indices que hoy tiene
   cada tabla sobre su columna `guest_id`.

Esto reescribe datos reales en las tres tablas de FK. Al volumen actual
del sistema (eventos comunitarios, no millones de filas) es seguro
hacerlo dentro de la transaccion de la migracion.

## Guest creation endpoints (`app/routers/guests.py`)

Dos endpoints simetricos, sin una ruta "por defecto" implicita:

**`POST /guests/mazmo`** (reemplaza al actual `POST /guests/`)

```python
class CreateGuestRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255, ...)
    instagram_username: str | None = None
```

Mismo flujo de siempre: lookup a Mazmo via `fetch_user_by_username`,
mismo mapeo de errores (404 si no existe en Mazmo, 504 si Mazmo no
responde, 502 si Mazmo devuelve error). El chequeo de duplicado pasa de
`session.get(Guest, user.mazmo_user_id)` (PK lookup) a
`session.exec(select(Guest).where(Guest.mazmo_user_id == user.mazmo_user_id)).first()`,
porque `mazmo_user_id` ya no es PK.

El campo `username` de `CreateGuestRequest` no se renombra: en el
contexto de este endpoint (`/guests/mazmo`) es claramente "el username de
Mazmo a buscar", igual que el parametro `username` de
`MazmoClient.fetch_user_by_username`. El rename a `mazmo_handle` aplica
al campo persistido/expuesto en `Guest`/`GuestPublic`, no a este input de
busqueda.

**`POST /guests/manual`** (nuevo)

```python
class CreateManualGuestRequest(BaseModel):
    displayname: str = Field(min_length=1, max_length=255)
    instagram_username: str | None = None
```

Crea el `Guest` directo, sin contactar Mazmo: `id=uuid4()`,
`mazmo_user_id=None`, `mazmo_handle=None`. Sin chequeo de duplicados: no hay
identificador externo contra el cual deduplicar. Se acepta que puedan
crearse dos guests con el mismo `displayname` (la deduplicacion /
merge de guests queda fuera de alcance, ver "Out of scope").

Ambos devuelven `GuestPublic`, actualizado a:

```python
class GuestPublic(BaseModel):
    id: uuid.UUID
    mazmo_user_id: int | None
    mazmo_handle: str | None
    displayname: str
    instagram_username: str | None
```

`GuestWithBanPublic` y `BannedGuestPublic` heredan/replican los mismos
campos y se actualizan igual (`mazmo_user_id`/`mazmo_handle` nullable,
`instagram_username` agregado, `id` agregado).

`GET /guests/` y `GET /guests/{guest_id}` no cambian de forma (solo el
tipo de `{guest_id}`, ver siguiente seccion). `GET /guests/by-username/{username}`
se renombra a `GET /guests/by-mazmo-handle/{mazmo_handle}`, consistente
con el nombre de campo nuevo y con la simetria `/guests/mazmo` vs
`/guests/manual`. Sigue sin devolver resultados para guests sin
`mazmo_handle`.

El `EventLog` de `GUEST_CREATED` que escriben ambos endpoints pasa a usar
`guest_id=guest.id` (el UUID interno), no `mazmo_user_id`.

## Link retroactivo a Mazmo

**`PATCH /guests/{guest_id}/link-mazmo`**, body `{"username": "..."}`:

- 404 si `guest_id` no existe.
- 409 si el guest ya tiene `mazmo_user_id` seteado ("ya esta vinculado a
  @X").
- Lookup en Mazmo con el mismo `fetch_user_by_username` y mismo mapeo de
  errores que la creacion.
- 409 si ese `mazmo_user_id` ya pertenece a **otro** guest existente en el
  sistema. Sin merge automatico: fusionar el historial de RSVPs, bans y
  audit log de dos guests es una operacion delicada que queda fuera de
  alcance de este cambio (ver "Out of scope").
- Si no hay conflicto: setea `mazmo_user_id`, `mazmo_handle` y `displayname`
  con los datos reales de Mazmo (pisa el nombre manual que hubiera
  cargado el staff). `instagram_username` no se toca.
- Escribe un `EventLog` con `event_type=GUEST_MAZMO_LINKED` en el mismo
  commit.

## Edicion de guest

**`PATCH /guests/{guest_id}`**, body con `displayname` e
`instagram_username` opcionales (partial update). No permite tocar
`mazmo_user_id` ni `mazmo_handle` (eso solo cambia via link-mazmo o sync). Sin
entrada en `event_log`: es una edicion cosmetica, no un evento de negocio
auditable como check-in o ban.

## Endpoints existentes: `{mazmo_user_id}` -> `{guest_id}` (UUID)

Cambia el tipo del path param (de `int` a `uuid.UUID`) y los
`session.get(Guest, ...)` / comparaciones de `guest_id` en:

- `GET /guests/{id}` (`app/routers/guests.py`)
- `POST .../guests/{id}/add-walkin` (`app/routers/meetups.py`)
- `POST .../guests/{id}/checkin` (`app/routers/meetups.py`)
- `POST .../guests/{id}/undo-checkin` (`app/routers/meetups.py`)
- `POST .../guests/{id}/payment` (`app/routers/meetups.py`)
- `POST .../guests/{id}/payment/undo` (`app/routers/meetups.py`)
- `POST .../guests/{id}/ban` (`app/routers/organizations.py`)
- `PATCH .../guests/{id}/unban` (`app/routers/organizations.py`)
- filtro `guest_id` en `GET /events` (`app/routers/events.py`)

No cambia la logica de negocio de estos endpoints, solo el tipo del
identificador. Los mensajes de error que hoy citan `mazmo_user_id`
directamente (ej. "Cannot check in: guest mazmo_user_id=... is not
RSVPed") pasan a citar el `displayname`/`mazmo_handle` del guest junto
con su `guest_id` interno, ya que `mazmo_user_id` puede no existir.

Tambien hay que actualizar el mensaje 409 del flujo de creacion por
Mazmo, que hoy referencia `POST /guests/` en su detail, y la docstring
del router (`app/routers/guests.py` lineas 1-9).

## `sync.py`

Hoy `_build_rsvps` arma `MeetupRsvp.guest_id = mazmo_user_id` directo,
porque hoy `mazmo_user_id` y el `guest_id` de la FK son el mismo valor.
Con `id` desacoplado, el sync necesita un paso adicional:

1. Upsert de guests igual que hoy
   (`ON CONFLICT (mazmo_user_id) DO NOTHING`), pero cada `Guest` nuevo se
   construye con `id=uuid4()` explicito antes del insert.
2. **Nuevo paso**: `SELECT id, mazmo_user_id FROM guests WHERE mazmo_user_id IN (...)`
   sobre el batch de la sync actual, para armar el mapeo
   `mazmo_user_id -> id` (cubre tanto guests preexistentes como los recien
   insertados).
3. `_build_rsvps` usa ese mapeo para poblar `MeetupRsvp.guest_id` (UUID)
   en vez del `mazmo_user_id` crudo.
4. `_update_cancelled_rsvps` compara usando el mismo mapeo en vez de
   comparar directo contra `MazmoUserId`.

El comportamiento no cambia (sigue siendo idempotente, sigue sin tocar
`has_arrived` / `has_paid` / etc.), pero es la parte del cambio con mas
riesgo si se implementa apurado.

## Out of scope

- **Merge de guests duplicados**: si un guest manual y un guest real de
  Mazmo terminan siendo la misma persona sin pasar por link-mazmo (por
  ejemplo, alguien crea el guest manual y despues aparece por sync con su
  cuenta real antes de que el staff lo vincule), quedan como dos filas
  separadas. No hay deteccion ni fusion automatica.
- **Auto-link durante el sync**: el sync no intenta matchear un guest
  manual existente contra un RSVP nuevo de Mazmo por nombre. El link
  siempre lo dispara el staff a mano via `link-mazmo`.
- **Validacion de formato del username de Instagram**: se guarda como
  texto libre (con strip de `@` inicial), sin verificar contra la API de
  Instagram.

## Testing

- Tests nuevos: crear guest manual, crear guest por Mazmo (con y sin
  `instagram_username`), link-mazmo exitoso, link-mazmo 409 (ya
  vinculado), link-mazmo 409 (mazmo_user_id ya usado por otro guest),
  editar guest (displayname / instagram_username), sync arma
  correctamente el mapeo `mazmo_user_id -> id` al crear RSVPs.
- Tests existentes que usan `mazmo_user_id` como identificador en URLs
  (checkin, ban, walk-in, payment, etc.) necesitan actualizarse para usar
  el nuevo `guest_id` (UUID). Es un cambio mecanico pero extenso; se debe
  cubrir en el plan de implementacion, no se detalla aca archivo por
  archivo.
- Tests existentes de `GET /guests/by-username/{username}` se mueven a
  `GET /guests/by-mazmo-handle/{mazmo_handle}`, mismo comportamiento.
