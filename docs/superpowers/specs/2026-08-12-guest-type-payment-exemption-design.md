# Categorias de guest exentas de pago + endpoint de stats del meetup

**Date:** 2026-08-12
**Status:** Approved

## Goal

En la meetup de Eros pasaron 3 casos que hoy el sistema no puede distinguir
de "todavia no pago":

1. Guests invitados personalmente por la organizadora del meetup, que no
   pagan entrada.
2. Guests que en realidad traen su propio stand de venta (vendors), no
   participan del evento como asistentes.
3. Personas que trabajan como staff del evento, tampoco pagan entrada.

Hoy `MeetupRsvp` solo tiene un booleano `has_paid`. No hay forma de marcar
"este guest no debe pagar" sin mentir marcando `has_paid = True` a mano, lo
cual mezcla "pago de verdad" con "esta exento de pagar" y hace perder el
motivo en el audit trail.

Este cambio agrega una categoria por-RSVP (`guest_type`) que exime
automaticamente del gate de pago en el check-in, y un endpoint de stats
para poder ver de un vistazo cuantos guests de cada categoria hubo en un
meetup.

Las categorias se deciden evento por evento (la misma persona puede ser
vendor en un evento y guest normal en el siguiente), por eso el campo vive
en `MeetupRsvp` y no en `Guest`.

## Data model changes (`app/models/models.py`)

Nuevo enum, junto a los demas enums de dominio (`EventType`, `PossibleRoles`):

```python
class GuestType(StrEnum):
    """Category of a guest's attendance at a specific meetup.

    NORMAL guests are subject to the meetup's requires_payment flag like
    any regular attendee. INVITED, VENDOR, and STAFF guests are exempt
    from the payment check-in gate regardless of has_paid: invited guests
    were personally invited by the meetup organizer and don't pay entry,
    vendors bring their own stand to sell goods and aren't attending as
    participants, and staff are working the event itself. This is set
    per-RSVP (not on the Guest) because these categories are decided
    event by event, not a persistent trait of the person.
    """

    NORMAL = "NORMAL"
    INVITED = "INVITED"
    VENDOR = "VENDOR"
    STAFF = "STAFF"
```

Nuevo campo en `MeetupRsvp`:

```python
guest_type: str = Field(default=GuestType.NORMAL.value, max_length=16, index=True)
```

Tipado como `str`, no como el `StrEnum` directamente: el repo guarda los
enums de dominio como `VARCHAR` con validacion solo del lado de Python
(mismo patron que `EventType` y `UserOrganization.role`), evitando que
SQLAlchemy mapee el campo a un `ENUM` nativo de Postgres.

`index=True` porque es un campo por el que se va a filtrar/reportar
(stats por categoria).

Nuevo valor de `EventType`:

```python
GUEST_TYPE_CHANGED = "GUEST_TYPE_CHANGED"
```

Sumar tambien `GUEST_TYPE_CHANGED` a `EventTypeFilter`
(`app/schemas/events.py`) para que sea filtrable desde el endpoint de
eventos.

### Documentacion a actualizar

`guest_type` pasa a ser, igual que `has_paid`, un campo curado a mano por
un admin y nunca tocado por el sync. Actualizar para reflejar esto:

- Docstring de `MeetupRsvp` en `models.py`.
- Comentario en `app/services/sync.py` sobre los campos que el upsert
  nunca sobreescribe.
- Regla 2 de `CLAUDE.md` ("el upsert NUNCA sobrescribe...").
- Docstring de `mark_guest_paid` en `meetups.py`, que hoy dice que el
  check-in se bloquea sin `has_paid=True` sin mencionar la excepcion de
  `guest_type`.

## Migration (Alembic)

Agregar la columna `guest_type` a `meetup_rsvps` con
`server_default='NORMAL'` para que las filas existentes backfilleen sin
necesidad de un paso de datos separado. Sigue el mismo patron que
`0015_meetup_payment_tracking.py` (agregar columna nueva con default,
sin tocar datos existentes).

Verificar despues de `alembic upgrade head` que la columna y su indice
quedaron creados correctamente (`\d meetup_rsvps` en psql) antes de dar
la migracion por terminada - la migracion 0016 (guest identity refactor)
perdio silenciosamente un UNIQUE constraint al recrear una columna, asi
que vale la pena el chequeo aunque este caso sea mas simple (solo agrega
una columna nueva, no recrea nada existente).

## Endpoint: cambiar guest_type

```
PATCH /organizations/{org_id}/meetups/{meetup_id}/guests/{guest_id}/type
```

- Permiso: `admin: User = Depends(get_org_admin)`.
- Body: `{"guest_type": "VENDOR"}` (schema `GuestTypeUpdateRequest`,
  valida contra los 4 valores del enum).
- Efecto: actualiza `rsvp.guest_type`. No toca `has_paid` / `paid_at` /
  `paid_by_id`.
- Audit trail en el mismo commit: `EventLog` con
  `event_type=EventType.GUEST_TYPE_CHANGED`, `actor_id=admin.id`,
  `guest_id`, `meetup_id`, `org_id`, y
  `reason=f"Changed guest_type from {old} to {new}"`.

## Cambio en el check-in: gate de pago

Gate actual (`app/routers/meetups.py:549`):

```python
if meetup.requires_payment and not rsvp.has_paid:
    raise HTTPException(status_code=409, detail=...)
```

Nuevo gate:

```python
if meetup.requires_payment and rsvp.guest_type == GuestType.NORMAL.value and not rsvp.has_paid:
    raise HTTPException(status_code=409, detail=...)
```

Solo se exige pago a guests `NORMAL`. `INVITED` / `VENDOR` / `STAFF` pasan
el check-in sin importar `has_paid`. El `.with_for_update()` que precede
al gate no se toca.

## Endpoint: stats del meetup

```
GET /organizations/{org_id}/meetups/{meetup_id}/stats
```

- Permiso: `_member: User = Depends(get_org_member)` (lectura, mismo nivel
  que ver la lista de guests - no requiere admin).
- Response: `MeetupStatsPublic`, agrupado en sub-objetos tematicos
  (patron ya usado en `EventLogPublic.actor` / `EventLogPublic.guest`
  en `app/schemas/events.py`):

```python
class AttendanceStats(SQLModel):
    total_rsvps: int
    arrived_count: int
    not_arrived_count: int
    walkin_count: int

class CancellationStats(SQLModel):
    cancelled_count: int
    cancelled_but_paid_count: int

class GuestTypeStats(SQLModel):
    normal_count: int
    invited_count: int
    vendor_count: int
    staff_count: int

class PaymentStats(SQLModel):
    paid_count: int
    unpaid_count: int
    exempt_from_payment_count: int

class MeetupStatsPublic(SQLModel):
    attendance: AttendanceStats
    cancellations: CancellationStats
    guest_types: GuestTypeStats
    payment: PaymentStats
```

### Formulas

Todas las queries filtran por `MeetupRsvp.meetup_id == meetup_id`. Se usa
`cancelled_rsvp` para separar "sigue anotado" de "dio de baja su RSVP
antes del evento".

| Campo | Filtro (ademas de `meetup_id`) |
|---|---|
| `attendance.total_rsvps` | `cancelled_rsvp = False` |
| `attendance.arrived_count` | `cancelled_rsvp = False AND has_arrived = True` |
| `attendance.not_arrived_count` | `cancelled_rsvp = False AND has_arrived = False` |
| `attendance.walkin_count` | `cancelled_rsvp = False AND is_walkin = True` |
| `cancellations.cancelled_count` | `cancelled_rsvp = True` |
| `cancellations.cancelled_but_paid_count` | `cancelled_rsvp = True AND has_paid = True` |
| `guest_types.normal_count` | `cancelled_rsvp = False AND guest_type = NORMAL` |
| `guest_types.invited_count` | `cancelled_rsvp = False AND guest_type = INVITED` |
| `guest_types.vendor_count` | `cancelled_rsvp = False AND guest_type = VENDOR` |
| `guest_types.staff_count` | `cancelled_rsvp = False AND guest_type = STAFF` |
| `payment.paid_count` | `cancelled_rsvp = False AND guest_type = NORMAL AND has_paid = True` |
| `payment.unpaid_count` | `cancelled_rsvp = False AND guest_type = NORMAL AND has_paid = False` |
| `payment.exempt_from_payment_count` | `cancelled_rsvp = False AND guest_type != NORMAL` |

Invariantes que deben cumplirse siempre:

- `guest_types.normal_count = payment.paid_count + payment.unpaid_count`
- `attendance.total_rsvps = guest_types.normal_count + guest_types.invited_count + guest_types.vendor_count + guest_types.staff_count`
- `attendance.total_rsvps = payment.paid_count + payment.unpaid_count + payment.exempt_from_payment_count`

`payment.paid_count` / `payment.unpaid_count` se limitan a `guest_type =
NORMAL` a proposito: si no, un guest exento que ademas tuviera
`has_paid = True` (por ejemplo, alguien que pago antes de que lo
reclasificaran como staff) se contaria dos veces y rompería las
invariantes de arriba.

`cancelled_but_paid_count` existe para poder identificar a quienes hay
que devolverles la entrada.

## Otros cambios de schema

`guest_type` se agrega tambien a `RsvpPublic`
(`app/schemas/guests.py`), consistente con como ya expone `has_paid`.

## Fuera de alcance

- No se toca `mark_guest_paid`, `undo_guest_payment`,
  `enable-payment`/`disable-payment`: son operaciones de registro de pago
  independientes del gate de exencion, y ya funcionan correctamente sin
  saber de `guest_type`.
- No se arregla el gap preexistente de `EventTypeFilter` /
  `_error_responses.py` respecto a otros valores de `EventType` ya
  faltantes (`GUEST_CREATED`, `PAYMENT_RECORDED`, etc.) - se agrega
  unicamente `GUEST_TYPE_CHANGED`, que es el valor nuevo que introduce
  este cambio.
- No se persiste el `guest_type` en `Guest` (nivel persona). Confirmado
  con el usuario que las categorias son puntuales por evento.

## Tests

Cobertura exhaustiva en 3 niveles. Este repo no tiene frontend, y nunca
mockea la DB (usa Postgres real de test - ver `CLAUDE.md`), asi que los
niveles se interpretan asi:

- **Unitario**: comportamiento aislado de una sola pieza (un schema, un
  default, un enum), armado con los helpers de `conftest.py`
  (`make_guest`, `make_meetup`, `make_rsvp`) sin pasar por HTTP.
- **Integracion**: un endpoint completo via `TestClient`, request/response
  contra la DB real de test, un escenario por test.
- **E2E**: flujos multi-endpoint que encadenan varias llamadas HTTP en un
  solo test, replicando el uso real de principio a fin (equivalente a lo
  que en un repo con frontend seria un test de browser, pero aca contra
  la API directamente).

### Unitario

- `test_new_rsvp_defaults_to_guest_type_normal_via_sync` - el sync crea
  RSVPs con `guest_type=NORMAL` cuando no se especifica nada.
- `test_new_rsvp_defaults_to_guest_type_normal_via_walkin` - idem para
  walk-ins.
- `test_guest_type_update_request_rejects_invalid_value` - el schema
  `GuestTypeUpdateRequest` rechaza un valor fuera del enum (422).
- `test_guest_type_enum_has_exactly_four_values` - guarda de regresion:
  si alguien agrega un valor al enum sin actualizar las formulas de
  stats, este test lo hace ruidoso en vez de fallar en silencio.
- `test_migration_backfills_existing_rsvps_with_guest_type_normal` (a
  nivel de migracion/DB, no HTTP) - RSVPs creadas antes de esta
  migracion quedan con `guest_type='NORMAL'` despues de
  `alembic upgrade head`, via el `server_default`.

### Integracion

**`PATCH .../guests/{guest_id}/type`:**

- `test_update_guest_type_returns_200_and_updates_rsvp` (admin, ej. a
  `VENDOR`).
- `test_update_guest_type_returns_403_for_staff_non_admin`.
- `test_update_guest_type_returns_401_without_auth`.
- `test_update_guest_type_returns_404_when_guest_not_rsvped_to_meetup`.
- `test_update_guest_type_returns_404_for_nonexistent_meetup`.
- `test_update_guest_type_returns_403_for_admin_of_different_org` -
  aislamiento multi-tenant: un admin de la Org A no puede cambiar
  `guest_type` en un meetup de la Org B.
- `test_update_guest_type_creates_audit_log_with_old_and_new_reason` -
  `EventLog.event_type == GUEST_TYPE_CHANGED`, `reason` menciona el
  valor viejo y el nuevo.
- `test_update_guest_type_does_not_modify_has_paid` - reclasificar un
  guest que ya habia pagado no le toca `has_paid`/`paid_at`/`paid_by_id`.
- `test_update_guest_type_back_to_normal` - round-trip `VENDOR -> NORMAL`.

**Check-in (gate de pago):**

- `test_checkin_blocks_normal_unpaid_guest_when_requires_payment` -
  regresion del comportamiento actual, sin tocar.
- `test_checkin_allows_invited_unpaid_guest_when_requires_payment`.
- `test_checkin_allows_vendor_unpaid_guest_when_requires_payment`.
- `test_checkin_allows_staff_unpaid_guest_when_requires_payment`.
- `test_checkin_allows_normal_paid_guest_when_requires_payment` -
  regresion, sigue funcionando igual.
- `test_checkin_allows_normal_guest_when_requires_payment_false` -
  regresion, el flag de meetup sigue mandando cuando no hay exencion.
- `test_checkin_allows_banned_guest_regardless_of_guest_type` -
  verificado con el usuario: el check-in **no** tiene (ni debe tener) un
  gate que bloquee guests baneados. Es intencional - el staff puede
  igual dejar entrar a un guest baneado, y el frontend usa el
  `is_banned` que ya expone la API de guests para mostrarle una
  advertencia antes de decidir. Este test confirma que un guest
  baneado con cualquier `guest_type` (incluido `NORMAL`, si ademas
  cumple el resto de las condiciones de pago) puede hacer check-in sin
  bloqueo - regresion explicita para que nadie agregue ese gate por
  error asumiendo que deberia existir (fue una suposicion incorrecta
  durante el diseno de este mismo spec, corregida aca).

**`GET .../meetups/{meetup_id}/stats`:**

- `test_meetup_stats_returns_200_with_grouped_shape` - la respuesta
  tiene los 4 sub-objetos (`attendance`, `cancellations`, `guest_types`,
  `payment`) con sus campos.
- `test_meetup_stats_returns_401_without_auth`.
- `test_meetup_stats_returns_403_for_member_of_different_org` -
  aislamiento entre organizaciones.
- `test_meetup_stats_returns_404_for_nonexistent_meetup`.
- `test_meetup_stats_returns_zero_counts_for_meetup_with_no_rsvps` -
  caso borde, meetup recien creado.
- `test_meetup_stats_counts_arrived_and_not_arrived_correctly`.
- `test_meetup_stats_counts_walkins_correctly`.
- `test_meetup_stats_excludes_cancelled_from_attendance_totals`.
- `test_meetup_stats_counts_cancelled_but_paid_correctly`.
- `test_meetup_stats_counts_all_four_guest_types_correctly` - un guest de
  cada tipo, verificar los 4 counters.
- `test_meetup_stats_counts_multiple_guests_per_type_correctly` - varios
  guests del mismo tipo (ej: 3 `VENDOR`) deben sumar en el counter
  correspondiente (`vendor_count == 3`), no solo detectar presencia.
  Cubre el caso de un `COUNT` mal escrito que solo detecta existencia.
- `test_meetup_stats_paid_and_unpaid_scoped_to_normal_guest_type` -
  regresion critica: un guest `VENDOR` con `has_paid=True` (caso raro
  pero posible) NO debe sumar a `payment.paid_count` ni a
  `payment.unpaid_count`, solo a `payment.exempt_from_payment_count`.
  Este es el bug de doble conteo que se descarto en el diseno.
- `test_meetup_stats_invariants_hold_across_mixed_fixture` - con un
  fixture que mezcla los 4 tipos, pagados/no pagados, cancelados,
  walk-ins, verificar numericamente las 3 invariantes documentadas
  arriba.

**Filtro de eventos:**

- `test_event_log_filters_by_guest_type_changed` - `EventTypeFilter`
  acepta `GUEST_TYPE_CHANGED` y el filtro devuelve solo esos eventos.

**Sync:**

- `test_sync_never_overwrites_existing_guest_type` - un guest ya
  clasificado como `VENDOR`, al correr el sync de nuevo (`ON CONFLICT`),
  mantiene `VENDOR` y no vuelve a `NORMAL`.

**Guest list:**

- `test_guest_list_response_includes_guest_type` - `RsvpPublic` expone
  el campo en `GET .../guests`.

### E2E (escenarios multi-endpoint)

- `test_eros_scenario_invited_vendor_staff_and_normal_guests_end_to_end` -
  el test insignia, replica el caso real que origino este feature en un
  solo flujo:
  1. Sync de un meetup con varios guests.
  2. Admin habilita `requires_payment`.
  3. Admin clasifica un guest como `INVITED`, otro como `VENDOR`, otro
     como `STAFF`; el resto queda `NORMAL`.
  4. Staff hace check-in de los 3 exentos sin pago -> 200 cada uno.
  5. Staff intenta check-in de un `NORMAL` sin pagar -> 409.
  6. Admin marca a ese guest como pagado; reintento de check-in -> 200.
  7. Un guest que ya habia pagado cancela su RSVP.
  8. Staff registra un walk-in.
  9. `GET .../stats` - verificar cada campo contra los numeros esperados
     a mano segun los pasos anteriores.
  10. `GET .../events?type=GUEST_TYPE_CHANGED` - verificar 3 entradas
      (INVITED, VENDOR, STAFF) con el `reason` correcto cada una.
- `test_reclassify_after_checkin_does_not_affect_already_checked_in_guest` -
  caso borde: un guest hace check-in como `NORMAL` habiendo pagado: el
  admin lo reclasifica despues como `VENDOR` retroactivamente; verificar
  que `has_arrived`/`arrival_time`/`arrival_order` no cambian, no hay
  re-check ni bloqueo retroactivo.
