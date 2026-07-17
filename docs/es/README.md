# AetherCal — documentación en español

AetherCal es infraestructura open source de calendario y agendamiento de citas: primero Python,
autoalojable con **un contenedor y una base de datos**.

| Guía | Qué cubre |
|---|---|
| [Guía de inicio](inicio.md) | Los conceptos: tenant, anfitrión, horario, tipo de evento, slot, reserva |
| [Quickstart](quickstart.md) | Levántalo desde cero y reserva una cita de prueba |

El resto de la documentación está en inglés:

| Guía | Qué cubre |
|---|---|
| [SDK de Python](../sdk.md) | `aethercal-client` — los clientes HTTP síncrono y asíncrono |
| [Componente de calendario](../calendar-component.md) | `@aethercal/calendar-react` y el wrapper Reflex `aethercal-ui` |
| [Webhooks](../webhooks.md) | Verificación de firma y el contrato de entrega **at-least-once** |
| [Embeber el widget](../embedding.md) | Pon el widget de reservas en cualquier sitio con un `<script>` |

## Estado

El stack de reservas está construido, probado y en producción para su primer operador. Es una
**0.1.0**, no una 1.0: el contrato de la API todavía puede cambiar.

Lo que **aún no está conectado** — no planifiques con ello:

- **Una reserva no crea el evento en el Google Calendar del anfitrión.** La verificación de
  ocupación *lee* su calendario, pero la escritura de vuelta no está conectada.
- **Los flujos de notificación funcionan, pero solo por email.** El motor, su migración y el
  recordatorio de 24 horas están vivos (y el recordatorio sí le llega al invitado), pero **WhatsApp
  y SMS están declarados sin ningún adaptador detrás**, y todavía no hay API ni pantalla para editar
  las reglas: se siembran, no se editan.
- **El no-show no emite webhook.** Puedes marcar una reserva como `no_show`, pero los eventos
  salientes siguen siendo `booking.created`, `booking.cancelled` y `booking.rescheduled`.
- **No hay pagos, ni aislamiento multi-negocio, ni reservas round-robin o colectivas.**

El detalle completo, verificado contra el código, está en el [README](../../README.md).
