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

- Default `NORMAL` al crear una RSVP nueva (via sync y via walk-in).
- `PATCH .../type`: permisos (admin OK, staff normal 403), guarda el
  audit log con el `reason` correcto, valores invalidos rechazados (422).
- Check-in: guest `INVITED`/`VENDOR`/`STAFF` con `has_paid=False` pasa el
  check-in en un meetup con `requires_payment=True`; guest `NORMAL` en
  las mismas condiciones sigue bloqueado (409).
- Stats: cada campo con datos de prueba que cubran los 4 `guest_type`,
  arrived/not-arrived, walk-in, cancelado con y sin pago; verificar que
  las 3 invariantes numericas cierran.
