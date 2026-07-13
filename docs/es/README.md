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
- **Los flujos de notificación están a medio camino.** El motor, su migración y la regla del
  recordatorio de 24 horas existen, pero solo el canal de **email** tiene adaptador: WhatsApp y SMS
  están declarados sin ninguna integración detrás.
- **No hay pagos, ni aislamiento multi-negocio, ni reservas round-robin o colectivas.**

El detalle completo, verificado contra el código, está en el [README](../../README.md).
