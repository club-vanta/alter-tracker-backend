# Historico de displayName de guests + sync que actualiza el nombre

**Date:** 2026-08-12
**Status:** Approved

## Goal

Hoy `Guest.displayname` solo se setea una vez, al insertar el guest via
sync (`ON CONFLICT (mazmo_user_id) DO NOTHING`). Si la persona cambia su
nombre en Mazmo despues, el sync nunca lo refleja: el docstring del
modelo ya reconoce esta tension ("displayname is highly mutable").

Ademas, otros 2 caminos SI sobreescriben `displayname` hoy sin dejar
rastro: `PATCH /guests/{id}` (edicion manual) y
`PATCH /guests/{id}/link-mazmo` (trae el nombre de Mazmo al vincular). El
docstring de unlink-mazmo dice explicitamente "no name history is kept".

Este cambio:

1. Hace que el sync actualice `displayname` cuando Mazmo reporta un
   valor distinto al que tenemos guardado (hoy no lo hace).
2. Agrega una tabla de historico que registra **todos** los valores que
   tuvo el `displayname` de un guest a lo largo del tiempo, sin importar
   si el cambio vino del sync, de una edicion manual, o de un
   link-mazmo.

## Data model changes (`app/models/models.py`)

```python
class GuestDisplaynameSource(StrEnum):
    """Origin of a recorded guest displayname value.

    SYNC: the Mazmo sync detected a different displayname than what we
    had stored, or inserted a brand new guest with this as its first
    value. MANUAL_EDIT: a staff/admin edited it via PATCH /guests/{id}.
    MAZMO_LINK: the value was set/overwritten by linking a Mazmo
    profile to a guest via PATCH /guests/{id}/link-mazmo. BACKFILL:
    historical entry created by the migration that introduced this
    table, for guests that already existed at that point; recorded_at
    is the migration's run time, not the guest's real creation time,
    because Guest has no created_at field to recover it from.
    """

    SYNC = "SYNC"
    MANUAL_EDIT = "MANUAL_EDIT"
    MAZMO_LINK = "MAZMO_LINK"
    BACKFILL = "BACKFILL"


class GuestDisplaynameHistory(SQLModel, table=True):
    __tablename__ = "guest_displayname_history"

    id: int | None = Field(default=None, primary_key=True)
    guest_id: uuid.UUID = Field(foreign_key="guests.id")
    displayname: str
    source: str = Field(max_length=16)
    actor_id: int | None = Field(default=None, foreign_key="users.id")
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

Es una linea de tiempo completa (una fila por cada valor que tuvo el
guest, incluido el primero al crearse), no pares viejo/nuevo. Reconstruir
"cambio de X a Y" es mirar la fila anterior por `recorded_at`.

`source` se guarda como `str` (no como el `StrEnum` directamente),
siguiendo la convencion del repo de no mapear estos enums a un `ENUM`
nativo de Postgres (mismo patron que `EventType`, `role`, y `guest_type`
del feature anterior).

`actor_id` es `NULL` para filas `SYNC`/`BACKFILL` (no hay un humano
detras), y se completa para `MANUAL_EDIT`/`MAZMO_LINK`.

Precedente de shape mas cercano en el repo: `OrganizationBan` (PK int,
`guest_id` FK, `banned_by_id` como actor, `banned_at` timestamp) - misma
estructura, no `EventLog`.

Nuevo indice compuesto `(guest_id, recorded_at)` - el patron de consulta
es siempre "todas las filas de este guest, ordenadas en el tiempo"; un
indice compuesto evita un sort aparte. No se indexa `displayname` en si,
no hay caso de uso de "buscar por nombre historico".

Nuevo valor de `EventType`:

```python
GUEST_DISPLAYNAME_CHANGED = "GUEST_DISPLAYNAME_CHANGED"
```

## EventLog tambien se actualiza

`EventLog.org_id` ya es nullable especificamente para eventos globales
de guest (`GUEST_CREATED`, `GUEST_MAZMO_LINKED`, `GUEST_MAZMO_UNLINKED`
ya loguean con `org_id=NULL`). Los 3 puntos de escritura de abajo
tambien crean un `EventLog(GUEST_DISPLAYNAME_CHANGED, org_id=None, ...)`
en el mismo commit que la fila de `GuestDisplaynameHistory`, con
`reason=f"Displayname changed from '{old}' to '{new}'"`.

En el caso de `link-mazmo`, esto queda junto al `EventLog(GUEST_MAZMO_LINKED)`
que ya se crea ahi mismo - dos eventos relacionados en el mismo commit,
uno por cada hecho (se vinculo Mazmo / cambio el nombre), en vez de un
tercer mecanismo desconectado.

## Migration (Alembic)

Sigue el patron de `0012_organization_bans.py` (crear tabla con
`sa.ForeignKeyConstraint`) + `op.create_index` aparte para el indice
compuesto (mismo patron que el indice compuesto de `0016` sobre
`event_log`).

Backfill en la misma migracion, sin batching (consistente con como
`0016` corre sus `UPDATE ... FROM` sin chunking a este volumen de
datos):

```sql
INSERT INTO guest_displayname_history (guest_id, displayname, source, actor_id, recorded_at)
SELECT id, displayname, 'BACKFILL', NULL, now()
FROM guests;
```

## Sync (`app/services/sync.py`)

`_upsert_guests()` deja de ser un `ON CONFLICT DO NOTHING` puro. Pasa a
un upsert atomico con `RETURNING`, para evitar una carrera: `Guest` es
una tabla global (no por-org), asi que dos syncs de meetups distintos
que comparten un guest podrian correr en paralelo. Un fetch-then-write
en dos pasos tendria una ventana de carrera; una sola sentencia atomica
no:

```sql
INSERT INTO guests (id, mazmo_user_id, mazmo_handle, displayname, ...)
VALUES (...)
ON CONFLICT (mazmo_user_id) DO UPDATE
  SET displayname = EXCLUDED.displayname
  WHERE guests.displayname IS DISTINCT FROM EXCLUDED.displayname
RETURNING id, displayname
```

Cada fila que devuelve el `RETURNING` (sea porque se inserto de cero, o
porque se actualizo por tener un nombre distinto) obtiene una fila nueva
en `GuestDisplaynameHistory` con `source=SYNC`, `actor_id=None`, mas el
`EventLog(GUEST_DISPLAYNAME_CHANGED, org_id=None)` correspondiente. Los
guests que no cambiaron no aparecen en el `RETURNING` y no generan
ninguna fila nueva - mismo comportamiento que el `DO NOTHING` de hoy
para ese caso.

## `PATCH /guests/{id}` (`app/routers/guests.py`)

Ya trae el guest completo antes de mutarlo (`_get_guest_or_404`), asi
que el valor anterior de `displayname` esta disponible sin trabajo
extra. El parametro de dependencia pasa de `_staff` (guionado, sin usar)
a `staff`, para poder usar `staff.id` como `actor_id`.

Es un endpoint de actualizacion parcial (`UpdateGuestRequest.displayname`
es opcional, solo se aplica `if "displayname" in payload.model_fields_set
and payload.displayname is not None`), asi que la comparacion "el valor
es distinto al actual" es significativa, no un caso trivial: si el
campo no viene en el request, no se toca nada y no se genera fila.

Si `displayname` cambia: insertar `GuestDisplaynameHistory`
(`source=MANUAL_EDIT`, `actor_id=staff.id`) y
`EventLog(GUEST_DISPLAYNAME_CHANGED, org_id=None, actor_id=staff.id)`
en el mismo commit que el update del guest.

## `PATCH /guests/{id}/link-mazmo`

Solo alcanzable cuando `guest.mazmo_user_id is None` (409 si ya esta
linkeado), asi que el `displayname` previo (el manual, si lo tenia) esta
disponible antes de sobreescribirlo con el de Mazmo. Si difiere:
insertar `GuestDisplaynameHistory` (`source=MAZMO_LINK`,
`actor_id=staff.id`) y `EventLog(GUEST_DISPLAYNAME_CHANGED, ...)`, en el
mismo commit que ya crea `EventLog(GUEST_MAZMO_LINKED, ...)`.

## Endpoint de lectura

```
GET /guests/{guest_id}/displayname-history
```

`Depends(get_approved_user)` - mismo patron que el resto de endpoints a
nivel guest en este router (`GET /guests/{id}`, `link-mazmo`,
`unlink-mazmo`), no es un endpoint por-org.

Response `GuestDisplaynameHistoryListResponse`, lista ordenada por
`recorded_at` descendente, cada entrada con `displayname`, `source`,
`recorded_at`, y `actor` (nombre del admin/staff cuando `actor_id` no es
`None`).

## Fuera de alcance

- No se toca `unlink-mazmo`: no cambia `displayname` hoy (docstring
  existente lo confirma), sigue sin cambiarlo.
- No se crea una tabla generica reutilizable de "historico de cualquier
  campo" - queda especifica a `displayname` (YAGNI); si aparece otro
  campo que necesite historico, se disena en su momento.
- No se implementa un mecanismo para deduplicar el race de sync
  concurrente entre corridas simultaneas de syncs de distintos meetups
  mas alla del `WHERE ... IS DISTINCT FROM` atomico ya descripto - eso
  ya elimina la ventana de carrera del todo, no hace falta nada extra.

## Tests

Mismos 3 niveles que el spec anterior (unitario con helpers directos a
la DB, integracion por endpoint via `TestClient`, E2E multi-endpoint).

### Unitario

- `test_new_guest_via_sync_creates_initial_history_row` - un guest nuevo
  insertado por sync genera 1 fila en `GuestDisplaynameHistory` con
  `source=SYNC`.
- `test_guest_displayname_source_has_exactly_four_values` - guarda de
  regresion del enum.
- `test_backfill_migration_creates_one_row_per_existing_guest` (a nivel
  de migracion/DB, no HTTP) - corriendo la migracion sobre datos de
  prueba, cada guest preexistente tiene exactamente 1 fila `BACKFILL`.

### Integracion

**Sync:**

- `test_sync_updates_displayname_when_mazmo_reports_different_value` -
  guest existente, Mazmo devuelve un nombre distinto, `Guest.displayname`
  se actualiza.
- `test_sync_does_not_update_displayname_when_unchanged` - mismo nombre,
  no se genera fila de historico ni de EventLog (regresion, evita ruido).
- `test_sync_creates_history_row_with_source_sync_on_change`.
- `test_sync_creates_history_row_with_source_sync_on_new_guest`.
- `test_sync_creates_eventlog_guest_displayname_changed_with_org_id_null`.
- `test_sync_never_downgrades_manual_or_link_history` - un guest con una
  fila `MANUAL_EDIT` mas reciente que lo que trae Mazmo: si el sync
  reporta un nombre distinto igual, se agrega una fila `SYNC` nueva (el
  sync no "sabe" de ediciones manuales, siempre refleja Mazmo) - test
  documenta este comportamiento esperado explicitamente.

**`PATCH /guests/{id}`:**

- `test_update_guest_displayname_creates_history_row_source_manual_edit`.
- `test_update_guest_displayname_creates_eventlog_entry`.
- `test_update_guest_without_displayname_field_creates_no_history_row` -
  el campo no viene en el body, no se toca nada.
- `test_update_guest_displayname_to_same_value_creates_no_history_row` -
  el admin "cambia" al mismo valor que ya tenia, no genera ruido.
- `test_update_guest_displayname_actor_id_matches_requesting_staff`.

**`PATCH /guests/{id}/link-mazmo`:**

- `test_link_mazmo_with_different_displayname_creates_history_row_source_mazmo_link`.
- `test_link_mazmo_with_same_displayname_creates_no_history_row`.
- `test_link_mazmo_creates_both_eventlog_entries` - `GUEST_MAZMO_LINKED`
  y `GUEST_DISPLAYNAME_CHANGED` en el mismo commit, cuando el nombre
  cambio.
- `test_link_mazmo_with_unchanged_displayname_creates_only_mazmo_linked_eventlog` -
  si el nombre no cambio, solo el evento de link, no el de displayname.

**`GET /guests/{guest_id}/displayname-history`:**

- `test_get_displayname_history_returns_200_ordered_by_recorded_at_desc`.
- `test_get_displayname_history_includes_source_and_actor`.
- `test_get_displayname_history_returns_404_for_nonexistent_guest`.
- `test_get_displayname_history_returns_single_row_for_guest_with_no_changes_since_creation` -
  un guest creado despues de esta migracion, sin cambios posteriores:
  la respuesta tiene exactamente 1 fila (la de creacion, `source=SYNC` o
  `MANUAL_EDIT` segun como se creo), no una lista vacia.
- `test_get_displayname_history_accessible_by_any_approved_staff` -
  no requiere pertenecer a una org especifica (dependencia global).

### E2E (escenarios multi-endpoint)

- `test_displayname_change_history_end_to_end` - flujo completo:
  1. Sync crea un guest nuevo con nombre "Juan".
  2. Un admin edita manualmente el guest a "Juan Perez"
     (`PATCH /guests/{id}`).
  3. Se corre el sync de nuevo, Mazmo ahora reporta "Juan P." - se
     actualiza.
  4. `GET .../displayname-history` - verificar las 3 entradas en orden
     correcto (mas reciente primero), cada una con el `source` esperado
     (`SYNC`, `MANUAL_EDIT`, `SYNC`), `actor` presente solo en la
     entrada manual.
  5. `GET .../events?type=GUEST_DISPLAYNAME_CHANGED` - verificar que
     aparecen las 2 entradas correspondientes a los cambios reales (no
     3, porque la creacion inicial via sync no es un "cambio").
- `test_link_mazmo_with_name_change_end_to_end` - guest creado
  manualmente con nombre "Ana" (sin Mazmo), despues se linkea a un
  perfil de Mazmo cuyo `displayname` es "Ana Garcia": verificar que
  queda 1 fila `MANUAL_EDIT` (creacion) + 1 fila `MAZMO_LINK`, y que el
  timeline de eventos tiene ambos `GUEST_MAZMO_LINKED` y
  `GUEST_DISPLAYNAME_CHANGED` en el mismo commit (mismo timestamp).
