# Contribuir a AetherCal

> La versión canónica de este documento es [`CONTRIBUTING.md`](../../CONTRIBUTING.md) (en inglés). Si
> ambas difieren, manda la inglesa.

Gracias por tu interés. AetherCal está en pre-alfa; ahora mismo la ayuda más valiosa es probar el
motor de agendamiento y reportar resultados incorrectos.

## Reglas base

- **Primero la correctitud.** El motor de agendamiento (`aethercal-core`) se desarrolla con tests
  primero (*test-first*). Todo cambio en fechas, recurrencia, zonas horarias, disponibilidad o
  cálculo de slots viene con tests —basados en propiedades cuando la invariante es general
  (`packages/aethercal-core/tests/`).
- **Mantén `core` puro.** `aethercal-core` no puede importar ningún otro paquete interno ni hacer
  I/O. Lo aplican contratos de importación en CI.
- **Un solo tema por pull request.** Diffs pequeños y revisables.
- **Conventional Commits.** Los mensajes de commit siguen la especificación Conventional Commits; el
  changelog se genera a partir de ellos.

## Cómo proponer un cambio

1. **Abre primero un issue para cualquier cosa no trivial** —un bug, un cambio de comportamiento, una
   capacidad nueva. Los arreglos triviales (una errata, un enlace roto) pueden ir directo a un pull
   request. Acordar el alcance antes de escribir código evita que un diff grande se rechace por
   dirección.
2. **Haz un fork y una rama** desde `main`. Mantén la rama enfocada en un solo tema.
3. **Trabaja test-first** cuando el cambio toque agendamiento, y mantén `aethercal-core` puro y sin
   I/O (ambas cosas se aplican en CI —ver abajo).
4. **Corre la compuerta local completa antes de hacer push:**

   ```bash
   uv run poe check     # ruff format, ruff check, pyright, contratos de importación, pytest
   ```

   Las mismas comprobaciones corren en CI (`.github/workflows/ci.yml`): lint + type-check, el guard
   de drift del bundle JS del calendario, la matriz de tests sobre Python 3.11–3.13 en Linux y
   Windows, la suite `-m db` contra PostgreSQL y un `docker build` de la imagen de despliegue. **Un
   pull request no se mergea hasta que CI esté en verde.**
5. **Abre el pull request** y completa la [plantilla](../../.github/pull_request_template.md): un
   resumen de una línea, el issue enlazado y tu evidencia de tests. Un solo tema por PR; sin secretos
   ni artefactos generados (`dist/`, `.venv/`, ruido del lockfile) commiteados.
6. **Un mantenedor revisa y mergea.** Cada ruta tiene un revisor obligatorio
   (ver [Gobernanza del proyecto](#gobernanza-del-proyecto)); espera comentarios de revisión y prepárate
   para iterar.

## Setup local

```bash
uv sync
uv run poe check
```

## Tests contra base de datos (`-m db`)

Casi toda la suite corre offline contra SQLite en memoria. Un conjunto más chico —paridad de
migraciones, el advisory lock de arranque, el índice único parcial, la atomicidad real del outbox—
solo puede probarse contra un PostgreSQL real, y está marcado `db`. Esos tests se **saltan** en la
corrida por defecto y necesitan un servidor:

```bash
AETHERCAL_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/aethercal_test \
  uv run pytest -m db
```

Pedir esa suite por nombre sin una base de datos es un error duro, no una corrida en verde de nada
(ver el `conftest.py` de la raíz del repositorio).

**Las corridas concurrentes de `-m db` son seguras.** Cada corrida crea su **propio esquema** de
PostgreSQL —nombrado por el proceso, el worker de `pytest-xdist` y un sufijo aleatorio—, apunta su
`search_path` ahí y lo elimina al salir (`apps/server/tests/conftest.py::pg_url`). Dos worktrees, o
dos workers de xdist, pueden así compartir una base de datos sin verse entre sí.

## Reportar bugs de agendamiento

Un buen reporte incluye la regla de recurrencia (o la configuración de disponibilidad), la ventana de
consulta, la zona horaria y las ocurrencias que esperabas frente a lo que obtuviste. Si se reproduce
en `aethercal-core`, ahí es donde van el arreglo y su test de regresión.

## Reportar un problema de seguridad

**No** abras un issue ni un pull request público para una vulnerabilidad. Repórtala en privado —el
proceso, el alcance y qué esperar están en [seguridad.md](seguridad.md) (o
[`SECURITY.md`](../../SECURITY.md), canónico).

## Licencia y firma (DCO)

AetherCal usa la [licencia MIT](../../LICENSE). Las contribuciones son **inbound = outbound**: al
abrir un pull request aceptas que tu contribución queda licenciada al proyecto y a sus usuarios bajo
la misma licencia MIT, y que tienes el derecho de otorgarla.

Usamos el [Developer Certificate of Origin](https://developercertificate.org/) en lugar de un CLA.
Certifica cada commit firmándolo —esto agrega una línea
`Signed-off-by: Tu Nombre <tu@example.com>` usando la identidad de tu configuración de Git:

```bash
git commit -s -m "fix: corrige el borde de DST en la expansión de slots"
```

La firma declara que escribiste el cambio, o que tienes el derecho de enviarlo bajo la licencia del
proyecto. Usa tu nombre real y un correo alcanzable.

## Gobernanza del proyecto

AetherCal lo mantiene un equipo pequeño de mantenedores, listado como los revisores obligatorios en
[`.github/CODEOWNERS`](../../.github/CODEOWNERS). Hoy es un único mantenedor; el modelo es
deliberadamente liviano para un proyecto pre-alfa y crecerá con las personas que contribuyan.

- **Las decisiones se toman en abierto** —en issues y pull requests, no en privado. Los cambios de
  fondo empiezan como un issue para acordar la dirección antes de escribir código.
- **Cada ruta tiene un revisor obligatorio**, y las rutas de mayor riesgo (el motor puro de
  agendamiento `packages/aethercal-core/` y todo lo que está bajo `.github/`) las revisa un mantenedor
  directamente. Ver [CODEOWNERS](../../.github/CODEOWNERS).
- **Las reglas de oro del código** —correctitud primero con tests, un `core` puro y sin I/O, un tema
  por PR, sin secretos en el fuente— las aplica CI, no la memoria. Un cambio que debilite un guard
  debe explicar por qué en el pull request.
- **Un mantenedor tiene la última palabra** sobre alcance y dirección, y es responsable de mantener
  el stack publicado coherente. El desacuerdo se resuelve en el hilo; si no se puede, el mantenedor
  decide y deja registrado el razonamiento.
