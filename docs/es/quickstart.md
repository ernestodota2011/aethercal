# Quickstart — autoaloja AetherCal y reserva una cita de prueba

De una máquina limpia a una reserva real y confirmada. AetherCal corre como **un contenedor más
PostgreSQL**, configurado enteramente por variables de entorno.

Casi todo el tiempo se lo lleva el primer `docker compose up --build`, que compila la imagen; los
pasos siguientes toman segundos.

**Necesitas:** Docker con el plugin Compose, y este repositorio.

Si aún no conoces el modelo (tenant, anfitrión, horario, tipo de evento, slot), lee antes la
[guía de inicio](inicio.md): son cinco minutos y te ahorra las dudas más comunes.

---

## 1. Configura

```bash
git clone https://github.com/ernestodota2011/aethercal.git
cd aethercal/deploy
cp .env.example .env
```

Hay dos valores obligatorios. Genera el secreto de la aplicación:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Edita `.env`:

- `AETHERCAL_APP_SECRET` — pega el valor que acabas de generar.
- `POSTGRES_PASSWORD` — elige una contraseña fuerte y pon **el mismo** valor dentro de
  `AETHERCAL_DATABASE_URL` (`postgresql://aethercal:<contraseña>@postgres:5432/aethercal`).

Todo lo demás trae un valor por defecto que funciona. SMTP y Google son opcionales: déjalos en
blanco y la aplicación arranca igual — las reservas funcionan, solo se saltan el email de
confirmación y la verificación de ocupación del calendario.

## 2. Levanta

```bash
docker compose up --build
```

La aplicación espera a que PostgreSQL esté sano, corre las migraciones y sirve. En otra terminal:

```bash
curl http://localhost:8000/api/v1/health
# {"status":"ok"}
```

## 3. Crea un tenant y una API key

```bash
docker compose exec app aethercal-admin create-tenant \
  --slug demo --name "Clínica Demo" --email dueno@example.com --timezone America/New_York
# tenant_id=035683a9-...
# user_id=54639215-...        <- este usuario es el anfitrión

docker compose exec app aethercal-admin issue-api-key --tenant-slug demo --name quickstart
# ack_....
```

La clave se imprime **una sola vez**: se guarda hasheada y no se puede recuperar. Cópiala ahora.

```bash
export AETHERCAL_KEY="ack_...."                        # la clave recién impresa
export AETHERCAL_URL="http://localhost:8000/api/v1"
export HOST_ID="54639215-..."                          # el user_id de create-tenant
```

## 4. Define cuándo estás disponible

Un **horario** es un patrón semanal. Las claves de día son `0` = lunes … `6` = domingo.

```bash
curl -X POST "$AETHERCAL_URL/schedules/" \
  -H "Authorization: Bearer $AETHERCAL_KEY" -H "Content-Type: application/json" \
  -d '{
        "name": "Lunes a viernes 9-5",
        "timezone": "America/New_York",
        "rules": {
          "0": [{"start": "09:00", "end": "17:00"}],
          "1": [{"start": "09:00", "end": "17:00"}],
          "2": [{"start": "09:00", "end": "17:00"}],
          "3": [{"start": "09:00", "end": "17:00"}],
          "4": [{"start": "09:00", "end": "17:00"}]
        }
      }'
```

Guarda el `id` que devuelve como `SCHEDULE_ID`.

## 5. Define qué se puede reservar

Un **tipo de evento** es una cita reservable: una duración, un anfitrión y un horario.

```bash
curl -X POST "$AETHERCAL_URL/event-types/" \
  -H "Authorization: Bearer $AETHERCAL_KEY" -H "Content-Type: application/json" \
  -d '{
        "host_id": "'"$HOST_ID"'",
        "schedule_id": "'"$SCHEDULE_ID"'",
        "slug": "llamada-intro",
        "title": "Llamada de presentación",
        "duration_seconds": 1800,
        "max_advance_seconds": 2592000
      }'
```

Guarda el `id` como `EVENT_TYPE_ID`. (`max_advance_seconds` es con cuánta antelación pueden
reservar: 30 días aquí.)

## 6. Pide los slots libres

```bash
curl -G "$AETHERCAL_URL/slots/" \
  -H "Authorization: Bearer $AETHERCAL_KEY" \
  --data-urlencode "event_type=$EVENT_TYPE_ID" \
  --data-urlencode "from=2026-07-13" \
  --data-urlencode "to=2026-07-20" \
  --data-urlencode "tz=America/New_York"
```

```json
{
  "availability": "ok",
  "slots": [
    {"start": "2026-07-13T13:00:00Z", "end": "2026-07-13T13:30:00Z"},
    {"start": "2026-07-13T13:30:00Z", "end": "2026-07-13T14:00:00Z"}
  ]
}
```

Los límites de cada slot vienen en **UTC**; `tz` es solo la zona en la que los pediste (las 9:00 de
Nueva York son las 13:00Z en julio).

`availability` vale `ok` únicamente cuando la ocupación externa era conocida y completa para toda la
ventana. Cualquier otro valor significa que un calendario conectado no respondió — y entonces
AetherCal deliberadamente **no ofrece ningún slot** de ese anfitrión, antes que arriesgar una doble
reserva.

## 7. Reserva

```bash
curl -X POST "$AETHERCAL_URL/bookings/" \
  -H "Authorization: Bearer $AETHERCAL_KEY" -H "Content-Type: application/json" \
  -d '{
        "event_type_id": "'"$EVENT_TYPE_ID"'",
        "start": "2026-07-13T13:00:00Z",
        "guest_name": "Jane Doe",
        "guest_email": "jane@example.com",
        "guest_timezone": "America/New_York"
      }'
```

```json
{
  "id": "5a13f24c-8e79-4240-b661-b8d4846fe01a",
  "status": "confirmed",
  "start": "2026-07-13T13:00:00",
  "end": "2026-07-13T13:30:00"
}
```

Eso es una cita real. Envía el mismo slot otra vez y AetherCal responde **`409 Conflict`**: el
conflicto lo decide la base de datos, no una carrera en la aplicación.

## 8. La página de reservas

La página pública corre como su propio servicio en el mismo compose, en <http://localhost:5001>.
Pon la API key en `AETHERCAL_API_KEY` dentro de `.env` (es la clave que la página presenta en nombre
del invitado — el invitado nunca ve una clave), reinicia, y tu tipo de evento queda reservable en
`/e/llamada-intro`.

Para ponerlo en tu propio sitio, mira [cómo embeber el widget](../embedding.md).

---

## A dónde ir ahora

- **[SDK de Python](../sdk.md)** — el mismo flujo en unas pocas líneas, con un
  [ejemplo ejecutable](../../examples/sdk/).
- **[Componente de calendario](../calendar-component.md)** — muestra las reservas en un calendario
  de verdad.
- **[Webhooks](../webhooks.md)** — entérate cuando se crea, cancela o reagenda una reserva. Lee el
  contrato **at-least-once** antes de escribir tu handler: vas a recibir duplicados.

## Problemas comunes

**`AETHERCAL_DATABASE_URL is not set`.** El contenedor corre sin tu `.env`. Ejecuta
`docker compose up` desde el directorio `deploy/`, que es donde vive ese archivo.

**La aplicación no conecta con PostgreSQL.** La contraseña dentro de `AETHERCAL_DATABASE_URL` debe
ser exactamente la misma que `POSTGRES_PASSWORD` en ese archivo. Son dos variables distintas y nada
comprueba que coincidan.

**Falta un slot que esperabas.** Las causas habituales: el día queda fuera de las reglas semanales
del horario; el hueco cae dentro de `min_notice_seconds`; está más allá de `max_advance_seconds`; o
el anfitrión ya está ocupado a esa hora — AetherCal lo considera ocupado en **todos** sus tipos de
evento a la vez.

**Todo responde `401`.** La API key va en la cabecera `Authorization: Bearer <key>`. Las claves no
son recuperables: si la perdiste, emite otra (`aethercal-admin issue-api-key`) y revoca la anterior
(`aethercal-admin keys revoke`).
