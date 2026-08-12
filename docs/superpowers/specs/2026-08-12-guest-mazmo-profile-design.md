# Perfil extendido de Mazmo (avatar, edad, genero, pronombre, suspended/banned)

**Date:** 2026-08-12
**Status:** Approved

## Goal

Se esta por diseñar una pagina de frontend para ver la informacion de un
guest. Para saber que datos hay disponibles, se hizo `curl` directo a la
API real de Mazmo (`GET /users/{username}` y `GET /users?ids=...`, el
mismo endpoint que usa el sync masivo) y se confirmo que el payload
incluye mucho mas de lo que hoy guardamos (`username`, `displayname`,
`id`): avatar, edad, genero, pronombre, y el propio sistema de
suspension/ban de Mazmo (`suspended`/`banned`), que **no tiene nada que
ver** con nuestro `OrganizationBan`.

Este cambio empieza a persistir 6 de esos campos: `avatar` (solo la URL
`default`), `age`, `gender`, `pronoun`, `suspended`, `banned`. Sin
historico - es un snapshot que se pisa en cada sync, a diferencia del
feature de `displayname` (`2026-08-12-guest-displayname-history-design.md`)
que si versiona cada valor.

## Data model changes (`app/models/models.py`)

```python
class GuestMazmoProfile(SQLModel, table=True):
    __tablename__ = "guest_mazmo_profile"

    guest_id: uuid.UUID = Field(foreign_key="guests.id", primary_key=True)
    avatar_url: str | None = Field(default=None)
    age: int | None = Field(default=None)
    gender: str | None = Field(default=None, max_length=32)
    pronoun: str | None = Field(default=None, max_length=32)
    mazmo_suspended: bool = Field(default=False)
    mazmo_banned: bool = Field(default=False)
    synced_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

Decisiones de shape:

- **Tabla separada, no columnas en `Guest`**: el nombre
  `guest_mazmo_profile` deja claro a nivel de schema que todo este
  bloque viene de Mazmo, no solo el naming de un campo suelto. Mantiene
  `Guest` (identidad) liviano frente a datos de perfil que van a seguir
  creciendo.
- **`mazmo_suspended`/`mazmo_banned` prefijados** incluso dentro de esta
  tabla ya-especifica-de-Mazmo: una vez que esto se serializa a JSON
  plano en la respuesta de un endpoint, el nombre del campo viaja solo
  sin el contexto de la tabla - alguien viendo `{"banned": false}` en el
  frontend podria confundirlo con nuestro `OrganizationBan`.
- **`gender`/`pronoun` como `str | None` libre**, no un enum nuestro:
  Mazmo controla ese vocabulario y puede agregar valores sin que nos
  enteremos (mismo criterio ya usado para no mapear `EventType`/`role` a
  un `ENUM` nativo de Postgres).
- **`avatar_url` guarda solo la URL `default`** del objeto anidado que
  devuelve Mazmo (que tiene 4 tamaños x 2 formatos) - alcanza para una
  imagen en una pagina admin, no hay caso de uso de responsive images.
- **`guest_id` como PK directa** (no un `id` surrogate aparte): es una
  relacion 1:1 real, y el guest_id ya identifica la fila sin ambiguedad.
  Es un patron nuevo en este repo (las tablas de asociacion existentes,
  `UserOrganization` y `MeetupRsvp`, usan PK compuesta de 2 FKs, no una
  FK unica como PK) - se documenta aca como una decision deliberada, no
  como algo que ya existia.
- **`synced_at`**: esta info puede cambiar en Mazmo entre sync y sync;
  este campo permite saber desde cuando es valido el snapshot mostrado.

Nueva relacion SQLModel en `Guest`:

```python
mazmo_profile: "GuestMazmoProfile | None" = Relationship(back_populates="guest")
```

## Migration (Alembic)

`op.create_table` para `guest_mazmo_profile`, con `guest_id` como PK y
FK a `guests.id`. Sin backfill: este dato solo se puede obtener con una
llamada en vivo a Mazmo, no hay forma de derivarlo de datos existentes.
La tabla arranca vacia y se va poblando a medida que cada guest
linkeado aparece en un sync futuro (o se re-linkea via link-mazmo).

**Limitacion conocida, aceptada**: un guest ya linkeado a Mazmo hoy pero
que no participa de ningun meetup proximo no va a tener fila en esta
tabla hasta que vuelva a aparecer en un sync. No se hace nada especial
para forzar ese backfill en este cambio.

## Cambios en el cliente de Mazmo (`app/schemas/mazmo.py`, `app/services/mazmo.py`)

`MazmoUserEntry` se extiende con los 6 campos nuevos, todos opcionales
(Mazmo puede no tenerlos seteados para un usuario):

```python
class MazmoAvatarEntry(BaseModel):
    default: str

class MazmoUserEntry(BaseModel):
    username: str
    displayname: str
    avatar: MazmoAvatarEntry | None = None
    age: int | None = None
    gender: str | None = None
    pronoun: str | None = None
    suspended: bool = False
    banned: bool = False
```

**Se unifica el parseo de los 2 endpoints de Mazmo que devuelven perfil
de usuario.** Hoy `fetch_users()` (batch, usado por el sync) valida con
`MazmoUserEntry.model_validate()`, pero `fetch_user_by_username()`
(lookup individual, usado por `link-mazmo`) arma un `NamedTuple` a mano
extrayendo solo `id`/`username`/`displayname` del JSON crudo,
descartando todo lo demas - si no se corrige, `link-mazmo` nunca tendria
acceso a los 6 campos nuevos por mas que se agreguen a `MazmoUserEntry`.

Se refactoriza `fetch_user_by_username()` para validar tambien con
`MazmoUserEntry.model_validate(data)`, y extraer el `id` numerico aparte
(el unico campo que el body de la respuesta individual trae pero el
schema comun no necesita duplicar, porque el endpoint batch lo pone
como clave del dict en vez de en el body). Un solo lugar (`MazmoUserEntry`)
define que campos se leen de Mazmo, evita que este mismo problema (un
campo que se agrega en un parser pero se olvida en el otro) se repita
en el futuro.

## Sync (`app/services/sync.py`)

Depende de que el sync ya resuelva `guest_id` (UUID interno) para cada
guest de la tanda - esto ya existe hoy via `_fetch_guest_id_map()`
(cubre todos los guests de `rsvps.keys()`, no solo los insertados/
modificados). El feature de `guest-displayname-history` reescribe
`_upsert_guests()`, asi que su implementacion debe preservar
`_fetch_guest_id_map()` (o un equivalente que cubra el mismo universo de
guests) para que este feature tenga de donde tomar los `guest_id`.

Con esos IDs resueltos, se hace un upsert masivo e incondicional (sin
gate de "solo si cambio", a diferencia de `displayname`) sobre
`guest_mazmo_profile` para **todos** los guests de la tanda, no solo los
que tuvieron un cambio de nombre:

```sql
INSERT INTO guest_mazmo_profile (guest_id, avatar_url, age, gender, pronoun, mazmo_suspended, mazmo_banned, synced_at)
VALUES (...)
ON CONFLICT (guest_id) DO UPDATE SET
  avatar_url = EXCLUDED.avatar_url,
  age = EXCLUDED.age,
  gender = EXCLUDED.gender,
  pronoun = EXCLUDED.pronoun,
  mazmo_suspended = EXCLUDED.mazmo_suspended,
  mazmo_banned = EXCLUDED.mazmo_banned,
  synced_at = EXCLUDED.synced_at
```

Sin esto, un guest cuyo `displayname` no cambio nunca actualizaria su
`mazmo_suspended`/`age`/etc., que pueden cambiar independientemente del
nombre.

## `PATCH /guests/{id}/link-mazmo`

El lookup individual (`fetch_user_by_username`) ya trae los 6 campos
nuevos gracias a la unificacion de parseo de arriba. En el mismo commit
que ya escribe `mazmo_user_id`/`mazmo_handle`/`displayname`, se hace el
mismo upsert de `GuestMazmoProfile` - sin ninguna llamada extra a
Mazmo, con datos que ya estan en memoria.

## `PATCH /guests/{id}/unlink-mazmo`

Ademas de `guest.mazmo_user_id = None` (comportamiento actual, sin
cambios), se borra la fila de `GuestMazmoProfile` de ese guest en el
mismo commit (delete-if-exists, no falla si todavia no habia sido
sincronizado). Mantener `age`/`gender`/`avatar` de una cuenta que ya no
esta vinculada podria mostrarse como si siguiera siendo valido. El
guard de ban-evasion existente (`guests.py`) corre antes de cualquier
mutacion, asi que este delete no interactua con esa logica.

## Exposicion en `GuestPublic` (`app/schemas/guests.py`)

```python
class GuestMazmoProfilePublic(SQLModel):
    model_config = ConfigDict(from_attributes=True)

    avatar_url: str | None
    age: int | None
    gender: str | None
    pronoun: str | None
    mazmo_suspended: bool
    mazmo_banned: bool
    synced_at: datetime
```

`GuestPublic.mazmo_profile: GuestMazmoProfilePublic | None = None` -
`None` tanto para guests sin link a Mazmo como para guests linkeados que
todavia no aparecieron en ningun sync (dos casos distintos, mismo
resultado `null` en la API).

**Correccion de N+1 obligatoria**: todo router que devuelva `GuestPublic`
(list y detail) debe agregar `.options(selectinload(Guest.mazmo_profile))`
a su query, siguiendo el mismo patron que ya usan `EventLogPublic.actor`/
`.guest` y `MeetupRsvp.guest` en otros routers. Puntualmente,
`GET /guests/` (`guests.py`) hoy hace un `session.exec(query).all()`
plano sin `.options(...)` - hay que agregarlo ahi explicitamente, si no
cada guest de la lista dispara una query aparte para su perfil.

## Fuera de alcance

- Sin historico/versionado de estos 6 campos - snapshot plano, decision
  explicita (a diferencia de `displayname`).
- Sin endpoint de "refrescar ahora" para un guest puntual fuera de sync/
  link-mazmo - se puede agregar despues junto con el diseño de la
  pagina de frontend si hace falta.
- Sin backfill retroactivo para guests ya linkeados hoy que no
  participen de un meetup proximo (ver limitacion conocida arriba).
- El diseño de la pagina de frontend en si (que campos mostrar, con que
  tratamiento dado que varios son datos personales sensibles) queda
  para una spec aparte - este cambio solo deja los datos disponibles
  via `GuestPublic.mazmo_profile`.

## Tests

Mismos 3 niveles que los specs anteriores.

### Unitario

- `test_mazmo_user_entry_parses_new_profile_fields` - `MazmoUserEntry.
  model_validate()` extrae correctamente `avatar.default`, `age`,
  `gender`, `pronoun`, `suspended`, `banned` de un payload con la forma
  real confirmada por curl.
- `test_mazmo_user_entry_tolerates_missing_optional_profile_fields` - un
  payload sin `age`/`gender`/`pronoun`/`avatar` valida igual, esos
  campos quedan `None`.
- `test_fetch_user_by_username_returns_full_profile_data` -
  `MazmoClient.fetch_user_by_username()` (httpx mockeado) devuelve el
  `id` numerico y los 6 campos de perfil, no solo `username`/
  `displayname` como antes de la unificacion.

### Integracion

**Sync:**

- `test_sync_creates_guest_mazmo_profile_for_new_guest`.
- `test_sync_updates_guest_mazmo_profile_on_subsequent_sync` - entre 2
  corridas de sync cambia `age`/`mazmo_suspended` en el mock de Mazmo;
  la fila se pisa (no se duplica), `synced_at` avanza.
- `test_sync_updates_guest_mazmo_profile_even_when_displayname_unchanged` -
  regresion critica: a diferencia del historico de `displayname`, esta
  tabla no tiene gate de "solo si cambio" - un guest cuyo nombre no
  cambio igual debe reflejar un `mazmo_suspended` nuevo si Mazmo lo
  reporta.
- `test_sync_handles_guest_with_missing_optional_profile_fields` - Mazmo
  omite `age`/`gender` para uno de los guests del batch, el sync no
  falla, quedan `NULL`.

**`link-mazmo`:**

- `test_link_mazmo_creates_guest_mazmo_profile_from_lookup_response` -
  el link puebla `GuestMazmoProfile` usando la respuesta del lookup
  individual, sin una segunda llamada a Mazmo (assert sobre el mock:
  llamado una sola vez).

**`unlink-mazmo`:**

- `test_unlink_mazmo_deletes_guest_mazmo_profile`.
- `test_unlink_mazmo_on_guest_without_profile_row_succeeds` - caso
  borde: un guest linkeado pero que todavia no fue sincronizado (sin
  fila de perfil) - el unlink no debe fallar por eso.

**Exposicion en `GuestPublic`:**

- `test_guest_detail_response_includes_mazmo_profile_when_linked_and_synced`.
- `test_guest_detail_response_mazmo_profile_null_when_not_linked_to_mazmo`.
- `test_guest_detail_response_mazmo_profile_null_when_linked_but_not_yet_synced` -
  caso borde distinto al anterior: `mazmo_user_id` esta seteado, pero
  todavia no corrio ningun sync ni link-mazmo que poblara el perfil.
- `test_guest_list_response_does_not_trigger_n_plus_1_for_mazmo_profile` -
  regresion de performance: con varios guests linkeados en la lista,
  contar las queries SQL ejecutadas (via el echo de sqlalchemy o un
  contador de statements) y verificar que `GET /guests/` no dispara una
  query adicional por guest.

### E2E (escenario multi-endpoint)

- `test_link_sync_unlink_mazmo_profile_lifecycle_end_to_end` - ciclo de
  vida completo:
  1. Guest manual existente, sin link a Mazmo.
  2. `link-mazmo` contra un perfil de Mazmo con datos completos ->
     `GuestMazmoProfile` se crea con los datos del lookup.
  3. `GET` detalle del guest -> `mazmo_profile` poblado correctamente.
  4. Corre un sync; Mazmo ahora reporta `suspended=true` y otra edad ->
     el perfil se actualiza en la misma fila (no se duplica).
  5. `GET` detalle de nuevo -> valores actualizados reflejados.
  6. `unlink-mazmo` -> `GuestMazmoProfile` se borra.
  7. `GET` detalle una vez mas -> `mazmo_profile` es `null`, el resto de
     los campos del guest (`displayname`, etc.) no se ven afectados.
