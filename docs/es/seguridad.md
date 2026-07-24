# Política de seguridad

> La versión canónica de este documento es [`SECURITY.md`](../../SECURITY.md) (en inglés). Si ambas
> difieren, manda la inglesa.

## Versiones soportadas

AetherCal está en pre-alfa. Hasta la primera versión etiquetada, solo se soporta la rama `main`.

## Reportar una vulnerabilidad

Reporta los problemas de seguridad **en privado** con el botón **"Report a vulnerability"** de GitHub
(Security → Advisories) en este repositorio —**no** en un issue, pull request o discusión público. Ese
canal es privado para los mantenedores y nos permite colaborar en el arreglo y, cuando esté listo,
publicar un aviso coordinado. Si no puedes usarlo, abre un issue normal que diga solo *"seguridad —
abran un canal privado"*, **sin detalles**, y te contactamos.

Un reporte útil incluye: el componente y la versión o commit afectados, una descripción del impacto y
el conjunto mínimo de pasos (o una prueba de concepto) que lo reproduce.

**Qué esperar:**

- **Acuse de recibo** en unos pocos días hábiles.
- **Una evaluación inicial** —aceptado / falta información / fuera de alcance, con su razonamiento—
  poco después, y actualizaciones a medida que avanza el arreglo.
- **Divulgación coordinada.** Danos un plazo razonable para publicar el arreglo antes de divulgar en
  público. Acordamos contigo una fecha de divulgación y publicamos un aviso (dándote crédito por tu
  nombre o alias, salvo que prefieras el anonimato) cuando el arreglo se libere.

AetherCal es pre-alfa y no tiene programa de recompensas; no hay pago, solo crédito.

## Alcance

**Dentro de alcance** —cualquier cosa que permita cruzar una frontera que el diseño promete mantener:

- El servidor (API, admin) y la página pública de reservas, tal como se construyen desde este
  repositorio.
- El artefacto de autoalojamiento tal como está documentado —la imagen de despliegue,
  `docker-compose.yml` y `provision_roles.sql`.
- Fallos de aislamiento entre negocios (que un negocio lea o escriba los datos de otro pese al
  row-level security de PostgreSQL), bypass de autenticación/autorización, falsificación de tokens de
  invitado, divulgación de secretos o credenciales, inyección, y SSRF más allá de la allowlist de
  egreso configurada.

**Fuera de alcance:**

- Vulnerabilidades en dependencias de terceros —repórtalas aguas arriba (un aviso de dependencia que
  nos afecte es bienvenido, pero el arreglo suele vivir en la dependencia).
- Cualquier cosa que exija que el **operador de la instancia** sea malicioso. El operador es de
  confianza por diseño: tiene `AETHERCAL_APP_SECRET` y puede descifrar la credencial de cualquier
  negocio (ver
  [Credenciales almacenadas](#credenciales-almacenadas-qué-protege-el-cifrado-y-qué-no)). "El
  operador puede leer los datos de su propia instancia" es el modelo de amenaza documentado, no un
  bug.
- Hallazgos que solo se reproducen bajo una configuración insegura contra la que la documentación
  advierte explícitamente —por ejemplo, exponer `/admin` en público sin rate-limiting en el proxy
  ([deploy/README.md](../../deploy/README.md)), o un `AETHERCAL_WEBHOOK_PRIVATE_TARGET_CIDRS`
  demasiado amplio.
- Endurecimiento de seguridad faltante sin un exploit concreto, denegación de servicio por volumen,
  ingeniería social y self-XSS.

## Qué no reportar

Estas son **limitaciones conocidas y documentadas**, no vulnerabilidades —ya están declaradas en la
documentación, así que un reporte diciéndonos que existen no agrega nada:

- **El teléfono del formulario público de reservas no se verifica como propiedad del invitado.** La
  verificación de posesión (un OTP o un enlace de confirmación) es un hueco declarado; los canales
  telefónicos vienen apagados por defecto y advierten al arrancar. Ver
  [phone-channels.md](../phone-channels.md).
- **Ningún proveedor de pagos se ha ejercitado contra una cuenta real**, y los reembolsos parciales
  no están modelados. Ver [byok-credentials.md](../byok-credentials.md).
- **Una sola llave cifra las credenciales de todos los negocios de una instancia** —cifrado en
  reposo, no aislamiento frente al operador (abajo).

La salida de un escáner automatizado sin impacto demostrado no es un reporte accionable —incluye un
hallazgo concreto y reproducible.

## Manejo de secretos

AetherCal nunca guarda secretos en el fuente. La configuración de la instancia —URLs de base de
datos, API keys, secretos de cliente OAuth y llaves de firma— se provee en tiempo de ejecución por
variables de entorno. Los enlaces de invitado son tokens firmados con expiración; las API keys se
guardan hasheadas; los webhooks salientes van firmados.

## Credenciales almacenadas: qué protege el cifrado (y qué no)

Cada negocio de una instancia puede traer sus propias credenciales de proveedor —cuenta de pago, relay
SMTP, WhatsApp, SMS. Se cifran en reposo con Fernet, bajo una llave derivada del único
`AETHERCAL_APP_SECRET` de la instancia.

**Una sola llave cifra las credenciales de todos los negocios de la instancia. Eso es cifrado en
reposo, no aislamiento criptográfico: quien opera la instancia puede descifrar la credencial de
cualquier negocio.** Protege contra un dump robado de la base, un backup filtrado o una lectura por
inyección SQL —ninguno de los cuales lleva el app secret. **No** protege contra el operador de la
instancia.

Entre negocios, el aislamiento lo aplica la base de datos (PostgreSQL `FORCE ROW LEVEL SECURITY`), así
que un negocio no puede leer las credenciales de otro ni siquiera con una consulta que olvidó filtrar.

Si necesitas que el operador no pueda descifrar tus credenciales, corre tu propia instancia. No hay
una llave por negocio implementada, y preferimos decirlo antes que dejar que la palabra "cifrado" lo
insinúe. El enunciado completo, y el procedimiento de rotación de llaves, están en
[byok-credentials.md](../byok-credentials.md).
