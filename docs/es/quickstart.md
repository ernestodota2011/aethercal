# Quickstart — autoaloja AetherCal y reserva una cita de prueba

De una máquina limpia a una reserva real y confirmada. AetherCal corre como **una imagen, cuatro
procesos y PostgreSQL**, configurado enteramente por variables de entorno.

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

Genera el secreto de la aplicación:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Edita `.env`:

- `AETHERCAL_APP_SECRET` — pega el valor que acabas de generar.
- `POSTGRES_PASSWORD` — el **superusuario de arranque** del contenedor de PostgreSQL. AetherCal nunca
  se conecta con él: es la identidad con la que creas los tres roles, en el paso 2.
- **Las tres contraseñas de rol.** AetherCal corre como tres usuarios distintos de PostgreSQL, y cada
  uno tiene su propia URL en `.env`: `AETHERCAL_DATABASE_URL` (`aethercal_app` — la API y el admin,
  sujetos a row-level security), `AETHERCAL_OWNER_DATABASE_URL` (`aethercal_owner` — las migraciones
  y la CLI) y `AETHERCAL_WORKER_DATABASE_URL` (`aethercal_worker` — el proceso de fondo). Elige una
  contraseña para cada uno y pégala dentro de su URL.

> **¿Por qué tres?** Los datos de un negocio se mantienen lejos de los de otro **por la base de
> datos**, no porque todo el mundo se acuerde de filtrar sus consultas. Eso solo funciona si el
> proceso que atiende las peticiones *no* es el dueño de las tablas — un dueño atraviesa sus propias
> políticas sin despeinarse. De ahí tres usuarios y tres URLs. Entre ellas no hay ningún respaldo, a
> propósito: una URL apuntando al usuario equivocado no daría error, simplemente leería nada — así
> que cada proceso le pregunta a la base de datos quién es al arrancar, y se niega a seguir si la
> respuesta no es la correcta.

Todo lo demás trae un valor por defecto que funciona. SMTP y Google son opcionales: déjalos en
blanco y la aplicación arranca igual — las reservas funcionan, solo se saltan el email de
confirmación y la verificación de ocupación del calendario.

## 2. Crea los tres roles de base de datos

Una sola vez, antes del primer arranque completo. `docker compose` lee `.env` automáticamente para
los *contenedores* — pero el comando de abajo corre `psql` desde **tu propia terminal**, que nunca ha
visto ese archivo. Cárgalo primero:

```bash
set -a; source .env; set +a
```

Levanta PostgreSQL solo y corre el script que ya viene en el repositorio:

```bash
docker compose up -d postgres

docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v db="$POSTGRES_DB" \
  -v pw_owner='<la contraseña del owner, la de tu .env>' \
  -v pw_app='<la contraseña de app>' \
  -v pw_worker='<la contraseña de worker>' \
  < sql/provision_roles.sql
```

Es un paso humano y no una migración, por una razón aburrida con un filo peligroso: crear un usuario
con permiso para saltarse las políticas de seguridad exige un superusuario, y las migraciones no
corren como tal.

## 3. Levanta

```bash
docker compose up --build
```

De la misma imagen salen cuatro procesos: un `migrate` de un solo uso (que lleva el esquema a la
última versión, como dueño) y después la `app`, el `worker` — recordatorios y webhooks salientes;
**es el proceso que realmente envía** — y la página pública de reservas (`booking`). En otra
terminal:

```bash
curl http://localhost:8000/api/v1/health
# {"status":"ok"}
```

## 4. Crea un tenant y una API key

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

## 5. Define cuándo estás disponible

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

## 6. Define qué se puede reservar

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

## 7. Pide los slots libres

`from`/`to` tiene que ser una ventana que todavía no haya pasado, así que calcúlala en vez de escribir
una fecha fija — es lo único de esta guía que caduca solo:

```bash
curl -G "$AETHERCAL_URL/slots/" \
  -H "Authorization: Bearer $AETHERCAL_KEY" \
  --data-urlencode "event_type=$EVENT_TYPE_ID" \
  --data-urlencode "from=$(date -u +%Y-%m-%d)" \
  --data-urlencode "to=$(date -u -d '+7 days' +%Y-%m-%d)" \
  --data-urlencode "tz=America/New_York"
```

(macOS trae el `date` de BSD, que no tiene `-d`: instala coreutils — `brew install coreutils` — y usa
`gdate`, o simplemente escribe un `to` de una semana en adelante.)

```json
{
  "availability": "ok",
  "slots": [
    {"start": "2026-07-20T13:00:00Z", "end": "2026-07-20T13:30:00Z"},
    {"start": "2026-07-20T13:30:00Z", "end": "2026-07-20T14:00:00Z"}
  ]
}
```

Las fechas de arriba son ilustrativas — las tuyas van a caer dentro de la ventana que pediste. Los
límites de cada slot vienen en **UTC**; `tz` es solo la zona en la que los pediste (las 9:00 de Nueva
York son las 13:00Z en julio).

`availability` vale `ok` únicamente cuando la ocupación externa era conocida y completa para toda la
ventana. Cualquier otro valor significa que un calendario conectado no respondió — y entonces
AetherCal deliberadamente **no ofrece ningún slot** de ese anfitrión, antes que arriesgar una doble
reserva.

## 8. Reserva

Toma uno de los valores `start` que tu **propia** respuesta del paso 7 acaba de devolver — no el del
ejemplo de arriba, que para cuando leas esto ya quedó en el pasado — y expórtalo:

```bash
export SLOT_START="2026-07-20T13:00:00Z"   # un "start" de TU respuesta del paso 7
```

```bash
curl -X POST "$AETHERCAL_URL/bookings/" \
  -H "Authorization: Bearer $AETHERCAL_KEY" -H "Content-Type: application/json" \
  -d '{
        "event_type_id": "'"$EVENT_TYPE_ID"'",
        "start": "'"$SLOT_START"'",
        "guest_name": "Jane Doe",
        "guest_email": "jane@example.com",
        "guest_timezone": "America/New_York"
      }'
```

```json
{
  "id": "5a13f24c-8e79-4240-b661-b8d4846fe01a",
  "event_type_id": "a15dfe22-9146-4e11-9e04-3b6cf1e57742",
  "start": "2026-07-20T13:00:00Z",
  "end": "2026-07-20T13:30:00Z",
  "status": "confirmed",
  "guest_name": "Jane Doe",
  "guest_email": "jane@example.com",
  "guest_timezone": "America/New_York",
  "guest_notes": null,
  "answers": {},
  "meeting_url": null,
  "rescheduled_from_id": null,
  "cancelled_at": null,
  "created_at": "2026-07-20T12:58:04Z"
}
```

Eso es una cita real. `start`/`end` vuelven con el sufijo `Z` (UTC), igual que la respuesta de los
slots de arriba — todas las fechas de la API son UTC. Envía el mismo `SLOT_START` otra vez y AetherCal
responde **`409 Conflict`**: el conflicto lo decide la base de datos, no una carrera en la aplicación.

## 9. La página de reservas

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
- **[Credenciales BYOK](../byok-credentials.md)** — ponle precio a un tipo de evento con la cuenta
  propia de Stripe o Mercado Pago de un negocio, antes de cobrar de verdad.

## Problemas comunes

**`AETHERCAL_DATABASE_URL is not set`.** El contenedor corre sin tu `.env`. Ejecuta
`docker compose up` desde el directorio `deploy/`, que es donde vive ese archivo.

**`psql: FATAL: role "..." does not exist` en el paso 2.** Corriste ese comando sin cargar `.env` en
esta terminal primero. `docker compose` lo lee para los contenedores; tu propia terminal nunca vio
`$POSTGRES_USER` / `$POSTGRES_DB` hasta que corres `set -a; source .env; set +a`.

**Un proceso se niega a arrancar y nombra un rol.** Un mensaje del estilo *"AETHERCAL_DATABASE_URL
connects as PostgreSQL role 'x', but this engine must run as 'aethercal_app'"* significa que una URL
apunta al usuario equivocado. Esa negativa es la función, no el fallo: bajo row-level security el
usuario equivocado no da error, simplemente no lee nada — así que cada proceso le pregunta a la base
de datos quién es, y se detiene.

**`migrate` termina con error, o la app nunca arranca.** El `migrate` de un solo uso corre como
`aethercal_owner`; si ese rol no existe, el paso 2 se saltó o se ejecutó contra otra base de datos.
`docker compose logs migrate` te dice cuál de las dos.

**La aplicación no conecta con PostgreSQL.** Cada una de las tres URLs lleva su propia contraseña, y
cada una tiene que coincidir exactamente con la que le pasaste a `provision_roles.sql` en el paso 2.
Son variables distintas y nada comprueba que coincidan.

**Las reservas funcionan, pero no llega ningún email ni webhook.** Eso lo envía el proceso `worker`,
no la API. `docker compose ps worker` — si no está corriendo, no se entregará nada nunca, y la API
seguirá pareciendo perfectamente sana mientras eso no ocurre.

**Falta un slot que esperabas.** Las causas habituales: el día queda fuera de las reglas semanales
del horario; el hueco cae dentro de `min_notice_seconds`; está más allá de `max_advance_seconds`; o
el anfitrión ya está ocupado a esa hora — AetherCal lo considera ocupado en **todos** sus tipos de
evento a la vez.

**Todo responde `401`.** La API key va en la cabecera `Authorization: Bearer <key>`. Las claves no
son recuperables: si la perdiste, emite otra (`aethercal-admin issue-api-key`) y revoca la anterior
(`aethercal-admin keys revoke`).
