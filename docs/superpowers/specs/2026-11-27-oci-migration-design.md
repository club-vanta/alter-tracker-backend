# Migracion de produccion: AWS EC2 -> OCI Always Free

Fecha: 2026-11-27. Estado: **aprobado e implementado** en `feat/oci-migration`. Pasos operativos: `docs/runbooks/oci-migration.md`.

## Decisiones despues de la aprobacion

- **Shape 1 OCPU / 6 GB**: la otra mitad del free queda para otros proyectos (por ejemplo un server de Minecraft, en su propio compartment). Costo: no hay lugar para levantar una instancia de reemplazo al lado; un rebuild es destruir y recrear (minutos de downtime, datos a salvo en el block volume).
- **La cuenta es Free Trial, no PAYG.** El limite de 16 OCPU es del trial. Al terminar el trial, Oracle borra lo que no sea Always Free (aca no hay nada) y aplica idle reclamation. Recomendacion: upgrade a PAYG (decision del dueno de la cuenta). Quotas y budget quedan igual.
- **OCI Bastion para shells de admin.** Run Command corre como `ocarun`, que solo puede `sudo alter-deploy`: asi la API key del deployer no da root. Para restore, cutover e incidentes se usa Bastion (free), con ingress al 22 solo desde la subnet y llaves efimeras (`infra/oci/scripts/admin-ssh.sh`).
- **Alarmas de logs sin Connector Hub.** Las lineas de la app llegan a OCI Logging como string dentro del campo `log` de docker, y el filtro de un connector sobre ese string no se pudo verificar. En su lugar, `deploy/host/push-metrics.sh` (cron cada 5 min) cuenta lineas `error`/`warning` con `docker logs` y publica metricas custom junto con el uso de disco. Una alarma avisa si esas metricas dejan de llegar.
- **Config de la instancia en metadata**, no en `user_data`: `deploy.sh` la lee del IMDS en cada deploy, asi cambiarla no recrea la instancia.
- **Bug corregido en backup**: sin `pipefail`, un `pg_dump` fallido subia un archivo truncado y reportaba exito. Test nuevo en `tests/infra/test_backup_restore.sh`.

## Contexto relevado

- Prod hoy: EC2 `t4g.small` (2 vCPU, 2 GB) en `us-east-1`. Uso real: 727 MB de RAM, disco 5.1/8 GB, DB `alter_event_tracker` de **10 MB** (Postgres 18.7).
- `/home/ec2-user/docker-compose.yml` del server coincide con `main` salvo las comillas de `PUBLIC_ALTERS_ORG_SLUG` (sin efecto). `deploy.sh` es identico.
- El codigo de la app no depende de AWS: structlog escribe JSON a stdout. El acople esta en `docker-compose.yml` (driver `awslogs`), `scripts/deploy.sh` (Secrets Manager), `docker/backup` (`aws s3 cp`), `infra/ec2-startup.sh` (cron que publica a SNS) e `infra/*.tf`.
- Tenancy OCI: vacia (sin compartments, VCNs ni instancias). Home region `sa-valparaiso-1`, un solo AD (`yalY:SA-VALPARAISO-1-AD-1`). Hay imagen `Oracle-Linux-9.8-aarch64-2026.11.20-0`.
- **El limite de A1 es 16 OCPU / 96 GB regional**, mas que los 2 OCPU / 12 GB free: la cuenta parece Pay As You Go. Todo lo que exceda Always Free se factura, asi que el diseno pone quotas y presupuesto como red de seguridad.
- El repo es **publico**: nada sensible puede ir en logs de Actions ni en archivos.

Fuentes leidas hoy: Always Free Resources, Running Commands on an Instance, Vault policy reference, Connector Hub `alarmlogs`, Custom Logs / Unified Monitoring Agent, OpenTofu backends, guia Terraform de Cloudflare Tunnel y docs del provider v5. No se pudieron leer (403/404): FAQ de Free Tier, price list, y el ejemplo de salida de `secret-bundle get`. Lo que depende de esas paginas esta marcado **[verificar]**.

## Diseno

### Computo

| Decision | Eleccion | Por que |
|---|---|---|
| Shape | `VM.Standard.A1.Flex`, **1 OCPU / 6 GB** | 3x la RAM actual. Deja libre la otra mitad del free (1 OCPU / 6 GB) para levantar una instancia de reemplazo al lado de la vieja (reconstruccion, cambio de imagen) sin pasarse del free. Shapes chicos consiguen capacidad mas facil. Se puede subir a 2/12 cambiando una variable. |
| SO | **Oracle Linux 9.8 aarch64** | Run Command (el deploy, ver abajo) solo soporta Oracle Linux / Autonomous Linux / CentOS / Windows; Ubuntu no. El Oracle Cloud Agent viene instalado. Docker CE se instala desde el repo oficial de Docker para RHEL/aarch64. |
| Boot volume | 50 GB (default) | |
| Datos | Block volume de 50 GB montado en `/var/lib/docker` | El volumen de Postgres y las imagenes viven fuera del boot volume: si hay que recrear la instancia, se reatacha el volumen. Total 100 GB de 200 free. |
| Backups de volumen | Policy propia: semanal, retiene 4 | 4 <= 5 backups free. |

### Red

- 1 VCN (de 2 free), 1 subnet publica con internet gateway (solo para egress) y **cero reglas de ingress** en la security list. firewalld queda con su default.
- **Cloudflare Tunnel** reemplaza el puerto 80 abierto: `cloudflared` corre como servicio del compose y sale por 7844 hacia Cloudflare, que enruta `api-alter-tracker.club-vanta.com` a `http://app:8000`. Consecuencias: no hay que abrir ningun firewall (desaparece el problema del doble firewall de OCI), la IP de la instancia deja de importar, y el tramo Cloudflare -> origen va cifrado (hoy, con SSL flexible, va en HTTP plano).
- Recursos (provider cloudflare v5): `cloudflare_zero_trust_tunnel_cloudflared` (`config_src = "cloudflare"`), `cloudflare_zero_trust_tunnel_cloudflared_config` (ingress + catch-all `http_status:404`), data source `cloudflare_zero_trust_tunnel_cloudflared_token`, y un `cloudflare_dns_record` CNAME a `<tunnel_id>.cfargotunnel.com`, proxied.
- El token de Cloudflare necesita ademas **Account > Cloudflare Tunnel: Edit**. Tunnel en el plan free: **[verificar]** (historicamente gratis).
- Acceso de emergencia: sin SSH abierto. Para operar se usa Run Command (equivalente a `ssm send-command`). OCI Bastion es free si alguna vez hace falta una shell.

### Secrets

- **OCI Vault** (vault default + master key software-protected: free; 150 secrets free).
- Secret `alter-tracker-backend-secrets` con el mismo JSON que hoy (`admin_username`, `admin_password`, `jwt_signing_key`, `db_user`, `db_password`). Tofu crea el vault y la key, **no el valor**.
- Secret `alter-tracker-backend-tunnel-token`, escrito por tofu desde el data source (el token ya queda en el state de todos modos).
- La instancia lee con **instance principal**: dynamic group que matchea su OCID + `Allow dynamic-group ... to read secret-bundles in compartment alter-tracker where target.vault.id = '<vault>'`.
- Copia AWS -> OCI sin que el valor pase por mi contexto: te doy un comando para que corras vos con `!`, que encadena `aws secretsmanager get-secret-value` con `oci vault secret update-base64` sin imprimir nada. Yo no corro `get-secret-value`.

### Logs

- Docker con driver `json-file` y rotacion (`max-size`, `max-file`): `docker logs` sigue andando y el disco no se llena.
- Plugin **Custom Logs Monitoring** (Unified Monitoring Agent) lee los JSON de los contenedores y los manda a OCI Logging: log group `alter-tracker-backend`, un custom log, retencion 30 dias. Cada linea lleva el servicio de compose via `labels`. Allowance free de Logging: **[verificar]** (se cree 10 GB/mes; hoy generamos muy poco).

### Alertas

- Topic de **Notifications** con suscripcion email a `infra-alerts@club-vanta.com` (1000 emails/mes free).
- Alarmas de Monitoring (free: 500M datapoints):
  - CPU > 80% por 10 min (`oci_computeagent` `CpuUtilization`).
  - Memoria > 85% (`MemoryUtilization`).
  - Disco > 80%: cron en la instancia que publica una metrica custom con `oci monitoring metric-data post` (instance principal). Reemplaza el cron que hoy publica a SNS.
  - **Instancia ausente**: `CpuUtilization[5m].absent()`. Detecta stop, crash o reclamation.
  - Logs `error` (>= 1 en 5 min) y `warning` (>= 10 en 10 min): **Connector Hub** Logging -> Monitoring con filtro por nivel, y alarmas sobre esas metricas. Usa 1 o 2 de los conectores free.
- UptimeRobot sobre `/health` sigue como esta.

### Backups de la DB

- Bucket de Object Storage `alter-tracker-backend-db-backups`, privado, lifecycle de 90 dias. Cuenta: 12 dumps/dia de < 1 MB -> < 1 GB en 90 dias y ~360 requests/mes (free: 20 GB y 50.000 requests).
- La imagen `docker/backup` pasa de `aws-cli` a **rclone** con el backend `oracleobjectstorage` y auth `instance_principal_auth` (`rclone rcat`, streaming como hoy). El destino es una env var `BACKUP_REMOTE`.
- Test nuevo en CI: buildear la imagen, correrla contra el Postgres de CI con `BACKUP_REMOTE` a un directorio local, y verificar que el dump restaura. Hoy esto no tiene test.
- Script `scripts/restore_backup.sh` para restaurar un dump. Lo usa el cutover y queda para incidentes.

### State de OpenTofu

- OpenTofu (1.11 local, docs 1.13) **no tiene backend `oci`**: el native backend de Oracle es solo de HashiCorp Terraform. Uso el **backend `s3` contra el endpoint S3-compatible** de Object Storage (`https://ax7nqesq2qs0.compat.objectstorage.sa-valparaiso-1.oraclecloud.com`), con una Customer Secret Key en un credentials file aparte (no en `~/.aws/credentials`).
- Bucket `alter-tracker-tofu-state` con versionado. Se crea una vez a mano con `oci os bucket create` (documentado), porque el state no puede vivir en algo que el mismo state crea.
- Locking: `use_lockfile` depende de `If-None-Match` en S3-compat, **[verificar]** con una prueba. Si no anda, queda sin lock (un solo operador) y lo documento.
- Root nuevo en `infra/oci/`, state aparte. `infra/` (AWS) queda intacto hasta el decommission.

### Seguridad de costos (la cuenta parece PAYG)

- Compartment `alter-tracker` con **quotas**: A1 tope 2 OCPU / 12 GB, block storage tope 200 GB, el resto de familias de computo en cero.
- **Budget** de USD 1 sobre el compartment, alerta al primer centavo real y al forecast.
- Todo recurso con el shape/tamano free explicito; nada de load balancers (el tunnel los vuelve innecesarios).

### Deploy automatico

Opciones:

1. **Run Command de OCI** (recomendada). El workflow, despues de buildear, llama a `CreateInstanceAgentCommand` con un script de pocas lineas, espera la ejecucion y luego hace curl a `/health`. Sin puertos de entrada, mismo modelo que `ssm send-command` que ya conocemos, y Actions muestra el resultado (ultimo 1 KB de salida; el log completo va a OCI Logging). Credencial: API key de un usuario IAM `alter-tracker-deployer` cuya unica policy es `manage instance-agent-command-family in compartment alter-tracker`. Si se filtra, lo maximo que se puede hacer es correr comandos en esa instancia, que es lo mismo que puede hacer quien tiene push a `main`.
2. SSH a traves del tunnel con service token de Cloudflare Access: suma una app de Access, una key SSH y `cloudflared` en el runner. Mas piezas para lo mismo.
3. Pull desde la instancia (timer que mira GHCR): no necesita credenciales en GitHub, pero Actions no se entera si el deploy fallo, y un push a `main` no queda verde/rojo segun el deploy.

Flujo con la opcion 1:

- `cd.yml`: los dos jobs de build quedan igual; nuevo job `deploy` con `needs` en ambos, `environment: production` y `concurrency: production` (no se pisan dos deploys). Tambien `workflow_dispatch` con input `sha` para rollback a un commit anterior.
- Se deploya el tag **`sha-<short>`**, no `latest`: rollback = redeployar un sha viejo.
- En la instancia, cloud-init instala `/usr/local/bin/alter-deploy <sha>`: baja `docker-compose.yml` y `scripts/deploy.sh` de `raw.githubusercontent.com/club-vanta/alter-tracker-backend/<sha>/` y lo corre. El usuario `ocarun` del agente solo tiene sudo para ese binario. Asi el compose y el deploy del server **siempre son los del commit deployado**: se termina el problema de "user_data se copio una vez".
- `deploy.sh`: lee secrets de Vault (instance principal), exporta `IMAGE_TAG`, `docker compose pull && up -d --remove-orphans` (migrate corre antes de app, como hoy), espera `127.0.0.1:8000/health`, y `docker image prune`.
- A futuro se podria cambiar la API key por federacion OIDC de GitHub con OCI si el tenancy lo soporta; no lo pude confirmar hoy.

### Idle reclamation

Oracle reclama una instancia Always Free si en 7 dias el p95 de CPU, la red y la memoria (A1) estan todos bajo 20%. Esta app puede calificar.

- Mitigacion principal: **cuenta Pay As You Go**. Se reporta ampliamente que no aplica a cuentas PAYG, pero la FAQ de Oracle dio 403 hoy: **[verificar]** en la consola que la cuenta este upgradeada. Las quotas y el budget de arriba cubren el riesgo de costo.
- Si igual pasa: la alarma de instancia ausente avisa; los datos estan en el block volume y en Object Storage; `tofu apply` + restore del ultimo dump reconstruye. El runbook se prueba en el ensayo (ver cutover).
- No propongo generar carga artificial para esquivar la regla.

### Out of host capacity

- Shape chico (1 OCPU / 6 GB) primero.
- Script `infra/oci/scripts/apply-with-retry.sh`: reintenta `tofu apply` con backoff solo si el error es `Out of host capacity`.
- Antes del apply, consultar el capacity report (`CreateComputeCapacityReport`, solo lectura) para ver si hay A1 disponible.
- PAYG tambien ayuda con la capacidad.

## Plan de cutover

Cada paso que crea algo o toca prod espera tu OK.

1. **Bootstrap OCI** (con tu OK): bucket de state, `tofu apply` de `infra/oci/` (compartment, quotas, budget, red, vault, logging, alarmas, tunnel, instancia). El tunnel publica primero un hostname de prueba, `api-alter-tracker-oci.club-vanta.com`. El hostname real no se toca.
2. **Secrets**: corres vos el comando de copia AWS -> Vault.
3. **Primer deploy** por `workflow_dispatch`: DB vacia, migrations, seed admin. Smoke test contra el hostname de prueba.
4. **Ensayo** (prod sigue en AWS): `pg_dump -Fc` de prod (solo lectura), subida a OCI con un PAR de escritura que vence en 1 hora, restore en OCI, comparacion de conteos por tabla y smoke test (login, listar meetups, ver un meetup). Se mide cuanto tarda.
5. **Cutover** (en una ventana sin evento, con tu OK, ~5-10 min de downtime):
   1. En EC2: `docker compose stop app weather-worker backup`. La DB queda arriba, sin escrituras.
   2. Dump final -> PAR -> restore en OCI (con `app` de OCI parado). Comparar conteos.
   3. Smoke test por el hostname de prueba.
   4. DNS: borrar A/AAAA de `api-alter-tracker` (root AWS) y crear el CNAME al tunnel (root OCI). Proxied, asi que el cambio es casi inmediato.
   5. `curl https://api-alter-tracker.club-vanta.com/health` + prueba desde el frontend.
6. **Observacion** 24-72 h. AWS: contenedores parados e instancia **stopped**, no terminada. S3, CloudWatch y Secrets Manager quedan.
7. **Decommission** (solo cuando confirmes, PR aparte): `tofu destroy` de AWS, borrar `start-instance.yml` / `stop-instance.yml`, mover el `cloudflare_zone_setting` al root OCI.

### Rollback

- Con EC2 todavia prendida: CNAME -> A/AAAA con las IPs de EC2 (no cambian mientras no se pare) y `docker compose start` en EC2. Si hubo escrituras en OCI, dump OCI -> EC2 con el mismo procedimiento.
- Con EC2 parada: `start-instance.yml` (la IPv4 cambia) y `tofu apply` del root AWS para actualizar el A.
- Del lado OCI, un deploy roto se revierte con `workflow_dispatch` y el sha anterior.

## Cambios en el repo (un PR)

- `infra/oci/`: root nuevo (providers `oci` + `cloudflare`, backend s3-compat, compartment, quotas, budget, VCN, instancia, block volume, policy de backup, vault, dynamic group, policies, usuario deployer, logging, connector, alarmas, notifications, bucket de backups, tunnel + DNS), `cloud-init.sh`, `scripts/apply-with-retry.sh`, `terraform.tfvars.example`.
- `docker-compose.yml`: `json-file`, `IMAGE_TAG`, servicio `cloudflared`, `app` sin puerto publico (`127.0.0.1:8000` para el health check local), backup con `BACKUP_REMOTE`.
- `scripts/deploy.sh`: Vault en vez de Secrets Manager, tag por sha, health check, prune.
- `docker/backup/`: rclone en vez de aws-cli, test en CI.
- `scripts/restore_backup.sh`.
- `.github/workflows/cd.yml`: job `deploy`, `workflow_dispatch`.
- `CLAUDE.md` y `README.md`: la seccion "Produccion" pasa a OCI (comandos de Run Command para agentes, Vault, logs).
- El codigo Python no cambia: no hay acople a AWS en `app/`. El unico codigo con logica nueva son los scripts de shell y su test.

## Preguntas abiertas (respondidas)

Respuestas: 1) Free Trial, no PAYG. 2) 1/6, para dejar lugar a otros proyectos. 3, 4 y 5) si.


1. Confirmas que la cuenta OCI es Pay As You Go? (Billing & Cost Management en la consola.)
2. Shape 1 OCPU / 6 GB, o preferis los 2 / 12 enteros?
3. Alertas al mismo `infra-alerts@club-vanta.com`?
4. Hostname de prueba `api-alter-tracker-oci.club-vanta.com` OK?
5. Podes agregar el permiso **Cloudflare Tunnel: Edit** al token de Cloudflare que usa tofu?
