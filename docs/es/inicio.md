# Guía de inicio

Antes de levantar nada, vale la pena entender las seis piezas del modelo. Son pocas y encajan en un
orden natural.

## El modelo

| Pieza | Qué es |
|---|---|
| **Tenant** | El negocio. Todo lo demás le pertenece y queda aislado dentro de él. |
| **Anfitrión** (host) | La persona con la que se agenda. Es un usuario del tenant. |
| **Horario** (schedule) | Cuándo está disponible: un patrón semanal (`0` = lunes … `6` = domingo) más excepciones por fecha. |
| **Tipo de evento** (event type) | Qué se puede reservar: una duración, un anfitrión y un horario. Por ejemplo, "Llamada de 30 min". |
| **Slot** | Un hueco libre. Se calcula; no se guarda. |
| **Reserva** (booking) | Una cita confirmada sobre un slot. |

Un slot **no existe** en la base de datos: se calcula en cada consulta a partir del horario, menos
lo que ya está ocupado, menos las reglas del tipo de evento (aviso mínimo, antelación máxima,
márgenes antes y después). Por eso no hay slots que se queden "pegados" ni que haya que regenerar.

## Las tres reglas que explican casi todo

**1. Un anfitrión está ocupado en todas partes.** Si alguien reserva una "Llamada de 30 min" a las
10:00, esa hora desaparece también de la "Consulta de 60 min" del mismo anfitrión. La ocupación es
de la persona, no del tipo de evento.

**2. Las horas viajan en UTC; se muestran en tu zona.** La API devuelve los límites de cada slot en
UTC, y `tz` es solo la zona en la que quieres leerlos. El componente de calendario, en cambio,
trabaja con hora local de pared (`"2026-07-13T09:00:00"`, sin offset). Convierte en tu frontera: la
API habla UTC, la grilla habla local.

**3. Reagendar no modifica la reserva: crea otra.** La nueva hereda la identidad de calendario
(`ical_uid`), la anterior queda `cancelled`, y la nueva apunta a ella con `rescheduled_from_id`. Si
guardas reservas de tu lado, sigue ese enlace en vez de suponer que el `id` se mantiene.

## Autenticación

Todo, salvo `/health`, exige una API key en la cabecera `Authorization: Bearer <key>`. Se emite con
el CLI de administración, se guarda **hasheada** y se imprime **una sola vez**: si la pierdes, emite
otra y revoca la anterior.

Esa clave la usa tu servidor. El invitado que reserva nunca ve una: la página pública de reservas la
presenta en su nombre. Y para que un invitado pueda **cancelar o reagendar** su propia cita,
AetherCal firma un *token de invitado* y lo pone en los enlaces de su email de confirmación — así ni
siquiera quien tiene la API key puede cancelar la cita de un tercero adivinando un `id`.

## Si el calendario conectado no responde

Cuando un anfitrión tiene un calendario externo conectado y AetherCal **no logra leerlo**, la
respuesta de slots llega con `availability` distinto de `"ok"` y **no se ofrece ningún slot** de ese
anfitrión.

Es deliberado: es preferible no ofrecer nada que arriesgar una doble reserva sobre un hueco que en
realidad estaba ocupado. Revisa `availability` antes de mostrar horarios.

Ojo con la distinción: un anfitrión que simplemente **no tiene** calendario conectado no cae en este
caso — sus slots se calculan con normalidad. La degradación aplica a "hay un calendario y no
responde", nunca a "no hay calendario".

## El siguiente paso

Levántalo y reserva una cita de prueba: **[Quickstart](quickstart.md)**.
