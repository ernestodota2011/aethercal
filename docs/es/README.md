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

Desde la 0.1.0 se conectaron los pagos (Stripe y Mercado Pago), el aislamiento multi-negocio por
row-level security de PostgreSQL, los flujos por WhatsApp y SMS, el webhook `booking.no_show` y la
escritura de vuelta al Google Calendar del anfitrión. La tabla de capacidades del
[README](../../README.md) es la lista completa y se contrasta contra el código.

Lo que **aún no está conectado** — no planifiques con ello:

- **Nada se ha ejercitado contra una cuenta real de un tercero.** La escritura al Google Calendar y
  los adaptadores de Stripe y Mercado Pago están cableados y cubiertos por tests contra un
  transporte *simulado*: eso demuestra que el código pide lo correcto, y no demuestra nada sobre lo
  que responde el proveedor real. Trata tu primera cuenta real como una integración que nadie ha
  corrido todavía.
- **WhatsApp y SMS funcionan, pero nada verifica que el teléfono sea de quien reserva.** Los
  adaptadores, la casilla de consentimiento y los topes diarios están vivos, y ambos canales siguen
  **apagados hasta que los configures**. El número, sin embargo, se escribe en un formulario
  *público*, y una casilla marcada solo prueba que *alguien* la marcó — no que el **dueño del
  número** aceptara. Verificar la posesión del número (un OTP, o un enlace de confirmación) es un
  **hueco declarado**, no algo que funcione en silencio. Lee
  [phone-channels.md](../phone-channels.md) antes de encender un canal telefónico.
- **Los reembolsos parciales no están modelados** — un reembolso siempre es el cargo completo.
- **No hay reservas round-robin ni colectivas.**
