# Runbook: migrar produccion de AWS EC2 a OCI

Lleva la API de la EC2 en `us-east-1` a la instancia A1 de OCI en `sa-valparaiso-1`, con CD automatico. El diseno y sus razones estan en `docs/superpowers/specs/2026-11-27-oci-migration-design.md`; aca solo esta que hacer.

Todos los comandos se corren desde la raiz del repo, en la maquina del mantenedor, salvo donde dice "en el servidor". Valores fijos de este setup: namespace de Object Storage `ax7nqesq2qs0`, perfil de AWS `base`, perfil de OCI `DEFAULT`. Si alguno cambio, corregilo antes de copiar.

Tiempo de downtime esperado en el paso 10: 5 a 10 minutos. Medilo en el ensayo (paso 9).

## Parte 1: levantar OCI en paralelo (sin downtime)

### 1. Crea el bucket del state de tofu

El state no puede vivir en algo que el mismo state crea, asi que este bucket va a mano, una sola vez, en el compartment raiz.

```bash
TENANCY=$(awk -F= '/^\[DEFAULT\]/{d=1;next} /^\[/{d=0} d&&$1~/^tenancy/{gsub(/ /,"",$2);print $2}' ~/.oci/config)
oci os bucket create --compartment-id "$TENANCY" --name alter-tracker-tofu-state \
  --versioning Enabled --public-access-type NoPublicAccess
```

### 2. Genera la Customer Secret Key para el backend s3

OpenTofu no tiene backend `oci`: usa el S3-compatible de Object Storage con una Customer Secret Key. Guardala en su propio archivo, no en `~/.aws/credentials` (ese es de AWS).

```bash
USER_OCID=$(awk -F= '/^\[DEFAULT\]/{d=1;next} /^\[/{d=0} d&&$1~/^user/{gsub(/ /,"",$2);print $2}' ~/.oci/config)
oci iam customer-secret-key create --user-id "$USER_OCID" --display-name tofu-state \
  --query 'data.{id:id,key:key}' --output json > /tmp/csk.json
install -m 600 /dev/null ~/.oci/tofu-state-credentials
jq -r '"[default]\naws_access_key_id = \(.id)\naws_secret_access_key = \(.key)"' /tmp/csk.json \
  > ~/.oci/tofu-state-credentials
rm /tmp/csk.json
```

La clave secreta solo se muestra al crearla. Si la perdes, crea otra (maximo 2 por usuario).

### 3. Genera la API key del deployer de GitHub

```bash
openssl genrsa -out ~/.oci/alter-tracker-deployer.pem 2048
chmod 600 ~/.oci/alter-tracker-deployer.pem
openssl rsa -pubout -in ~/.oci/alter-tracker-deployer.pem -out ~/.oci/alter-tracker-deployer_public.pem
```

La privada va a GitHub en el paso 7; la publica la sube tofu.

### 4. Completa `terraform.tfvars` y aplica `infra/oci`

ANTES: no existe `infra/oci/terraform.tfvars`.

DESPUES:

```hcl
# infra/oci/terraform.tfvars
tenancy_ocid             = "ocid1.tenancy.oc1..<el de ~/.oci/config>"
deployer_public_key_path = "~/.oci/alter-tracker-deployer_public.pem"
cloudflare_account_id    = "<igual que en infra/terraform.tfvars>"
cloudflare_api_token     = "<igual que en infra/terraform.tfvars>"
```

El token de Cloudflare necesita **Account > Cloudflare Tunnel: Edit** ademas de DNS Edit.

```bash
cd infra/oci
tofu init
tofu plan -out plan.tfplan   # revisalo: todo debe ser Always Free
tofu apply plan.tfplan
```

Si falla solo la instancia con `Out of host capacity` (Valparaiso tiene un solo AD), deja corriendo el reintento y despues aplica el resto:

```bash
scripts/apply-with-retry.sh   # reintenta cada 10 min hasta 8 horas
tofu apply
```

### 5. Confirma el email de alertas

OCI manda un mail de confirmacion a `infra-alerts@club-vanta.com`. Hasta que no lo aceptes, las alarmas no avisan a nadie.

### 6. Carga los secrets de la app en Vault

Tofu creo el secret con `{}`. Copia el JSON de AWS sin que pase por pantalla:

```bash
SECRET_ID=$(tofu -chdir=infra/oci output -raw app_secret_id)
oci vault secret update-base64 --secret-id "$SECRET_ID" \
  --secret-content-content "$(AWS_PROFILE=base aws secretsmanager get-secret-value \
    --region us-east-1 --secret-id alter-tracker-backend/secrets \
    --query SecretString --output text | base64 -w0)" >/dev/null
```

Correlo vos (con `!` si estas en Claude Code): un agente no debe leer secrets. Se mantienen `db_user` y `db_password` iguales a los de AWS, asi el dump restaurado sigue funcionando.

### 7. Configura el environment `production` en GitHub

```bash
cd infra/oci
gh secret set OCI_CLI_USER        --env production --body "$(tofu output -raw deployer_user_id)"
gh secret set OCI_CLI_FINGERPRINT --env production --body "$(tofu output -raw deployer_key_fingerprint)"
gh secret set OCI_CLI_TENANCY     --env production --body "$TENANCY"
gh secret set OCI_CLI_KEY_CONTENT --env production < ~/.oci/alter-tracker-deployer.pem
gh variable set OCI_CLI_REGION     --env production --body sa-valparaiso-1
gh variable set OCI_COMPARTMENT_ID --env production --body "$(tofu output -raw compartment_id)"
gh variable set OCI_INSTANCE_ID    --env production --body "$(tofu output -raw instance_id)"
gh variable set API_HEALTH_URL     --env production --body "$(tofu output -raw test_url)"
cd ../..
```

`API_HEALTH_URL` apunta al hostname de prueba hasta el cutover (paso 10.6).

### 8. Hace el primer deploy

Espera unos 30 minutos desde que se creo la instancia: el agente de Run Command tarda en empezar a leer comandos (dato de la doc de Oracle).

```bash
gh workflow run deploy.yml -f sha="$(git rev-parse origin/main)"
gh run watch "$(gh run list --workflow deploy.yml --limit 1 --json databaseId -q '.[0].databaseId')"
curl -s https://api-alter-tracker-oci.club-vanta.com/health
```

La DB arranca vacia: migrate crea el esquema y seed crea el admin. Si falla, el log completo esta en OCI Logging (log group `alter-tracker-backend`, log `server`) o en el servidor:

```bash
infra/oci/scripts/admin-ssh.sh 'sudo tail -50 /var/log/alter-tracker/deploy.log'
```

Despues prende el deploy automatico en cada push a `main`:

```bash
gh variable set OCI_DEPLOY_ENABLED --body true
```

### 9. Ensaya el traspaso de la base (prod sigue en AWS)

Es el paso 10.2 a 10.4 sin parar nada en AWS. Sirve para medir el tiempo y probar que el restore anda. La DB de OCI se pisa, no importa.

Crea un PAR (URL de escritura que vence en 1 hora) para subir el dump:

```bash
NS=ax7nqesq2qs0
BUCKET=alter-tracker-backend-db-backups
PAR=$(oci os preauth-request create --namespace "$NS" --bucket-name "$BUCKET" \
  --name cutover-rehearsal --access-type ObjectWrite --object-name cutover/rehearsal.dump \
  --time-expires "$(date -u -d '+1 hour' +%FT%TZ)" --query 'data."full-path"' --raw-output)
```

Dump en la EC2 (solo lectura, `pg_dump`) y subida directa al PAR, por SSM:

```bash
CID=$(AWS_PROFILE=base aws ssm send-command --region us-east-1 \
  --instance-ids i-054c0dd0cd6c37dc5 --document-name AWS-RunShellScript \
  --comment "OCI cutover rehearsal dump" \
  --parameters "commands=[\"docker exec ec2-user-db-1 sh -c 'pg_dump -Fc -U \\\"\$POSTGRES_USER\\\" alter_event_tracker' | curl -fsS -X PUT --data-binary @- '$PAR'\"]" \
  --query Command.CommandId --output text)
sleep 15
AWS_PROFILE=base aws ssm get-command-invocation --region us-east-1 --command-id "$CID" \
  --instance-id i-054c0dd0cd6c37dc5 --query '[Status,StandardErrorContent]' --output text
```

Restore en OCI y comparacion de conteos:

```bash
infra/oci/scripts/admin-ssh.sh 'set -e
  sudo /opt/oci-cli/bin/oci os object get --auth instance_principal --namespace ax7nqesq2qs0 \
    --bucket-name alter-tracker-backend-db-backups --name cutover/rehearsal.dump --file /tmp/rehearsal.dump
  sudo docker stop alter-tracker-app-1 alter-tracker-weather-worker-1 alter-tracker-backup-1
  sudo /opt/alter-tracker/current/scripts/restore_backup.sh --yes /tmp/rehearsal.dump
  sudo docker start alter-tracker-app-1 alter-tracker-weather-worker-1 alter-tracker-backup-1
  rm -f /tmp/rehearsal.dump'
```

Conteos del origen, para comparar con los que imprimio el restore:

```bash
SQL=$(base64 -w0 scripts/sql/row_counts.sql)
CID=$(AWS_PROFILE=base aws ssm send-command --region us-east-1 \
  --instance-ids i-054c0dd0cd6c37dc5 --document-name AWS-RunShellScript --comment "row counts" \
  --parameters "commands=[\"echo $SQL | base64 -d | docker exec -i ec2-user-db-1 sh -c 'psql -U \\\"\$POSTGRES_USER\\\" -d alter_event_tracker'\"]" \
  --query Command.CommandId --output text)
sleep 5
AWS_PROFILE=base aws ssm get-command-invocation --region us-east-1 --command-id "$CID" \
  --instance-id i-054c0dd0cd6c37dc5 --query StandardOutputContent --output text
```

Smoke test contra `https://api-alter-tracker-oci.club-vanta.com`: login, listar meetups, abrir un meetup. Anota cuanto tardo todo.

## Parte 2: cutover (con downtime)

Elegi un momento sin evento. Avisa al staff.

### 10. Hace el traspaso

1. **Para las escrituras en AWS.** La DB queda arriba para el dump.

   ```bash
   AWS_PROFILE=base aws ssm send-command --region us-east-1 --instance-ids i-054c0dd0cd6c37dc5 \
     --document-name AWS-RunShellScript --comment "cutover: stop writers" \
     --parameters 'commands=["docker stop ec2-user-app-1 ec2-user-weather-worker-1 ec2-user-backup-1"]'
   ```

2. **Dump final y subida**: igual que en el paso 9, con un PAR nuevo y `--object-name cutover/final.dump`.
3. **Restore en OCI**: igual que en el paso 9, con `cutover/final.dump`. Los conteos tienen que coincidir exactamente con los del origen (paso 9, ultimo bloque). Si no coinciden, NO sigas: rollback (abajo).
4. **Smoke test** contra el hostname de prueba.
5. **DNS**: saca los A/AAAA de AWS y crea el CNAME del tunnel, en este orden (Cloudflare no deja un CNAME junto a un A con el mismo nombre):

   ANTES (`infra/terraform.tfvars` y `infra/oci/terraform.tfvars`): sin `serve_from_aws` ni `serve_production_hostname`.

   DESPUES:

   ```hcl
   # infra/terraform.tfvars
   serve_from_aws = false
   ```

   ```hcl
   # infra/oci/terraform.tfvars
   serve_production_hostname = true
   ```

   ```bash
   tofu -chdir=infra apply      # solo debe borrar los dos cloudflare_dns_record
   tofu -chdir=infra/oci apply  # CNAME + regla de ingress del tunnel
   ```

   Si el plan del root AWS quiere tocar algo mas que esos dos registros, frena y averigua por que antes de aplicar.

6. **Verifica y apunta el health check del CD a produccion**:

   ```bash
   curl -s https://api-alter-tracker.club-vanta.com/health
   gh variable set API_HEALTH_URL --env production --body https://api-alter-tracker.club-vanta.com/health
   ```

   Proba el frontend como staff.

### 11. Apaga AWS (sin destruir)

Despues de 24 a 72 horas sin problemas:

```bash
AWS_PROFILE=base aws ssm send-command --region us-east-1 --instance-ids i-054c0dd0cd6c37dc5 \
  --document-name AWS-RunShellScript --comment "post-cutover: stop stack" \
  --parameters 'commands=["docker compose -f /home/ec2-user/docker-compose.yml stop"]'
gh workflow run stop-instance.yml
```

S3 (backups), CloudWatch y Secrets Manager quedan como estan. El `tofu destroy` del root AWS va en un PR aparte, cuando se confirme que no se vuelve.

## Rollback

**Antes del paso 11 (EC2 prendida):**

1. Revertir el DNS: `serve_production_hostname = false` y `tofu -chdir=infra/oci apply`; despues `serve_from_aws = true` y `tofu -chdir=infra apply`. Las IPs de la EC2 no cambiaron.
2. `docker start ec2-user-app-1 ec2-user-weather-worker-1 ec2-user-backup-1` por SSM.
3. Si hubo escrituras en OCI despues del cutover, traerlas: dump en OCI (`admin-ssh.sh 'sudo docker exec alter-tracker-db-1 sh -c "pg_dump -Fc -U \$POSTGRES_USER alter_event_tracker"' > final-oci.dump`) y restore en la EC2 con el mismo `restore_backup.sh` (`DB_CONTAINER=ec2-user-db-1`).

**Despues del paso 11:** `gh workflow run start-instance.yml` (la IPv4 cambia), y despues el paso 1 de arriba: `tofu -chdir=infra apply` toma la IP nueva.

**Deploy roto en OCI:** `gh workflow run deploy.yml -f sha=<sha anterior que andaba>`.

## Si Oracle reclama o pierde la instancia

La alarma `instance-absent` avisa. Los datos estan en el block volume (con `prevent_destroy`) y en los dumps del bucket.

```bash
cd infra/oci
tofu apply -replace=oci_core_instance.app   # o scripts/apply-with-retry.sh si no hay capacidad
gh variable set OCI_INSTANCE_ID --env production --body "$(tofu output -raw instance_id)"
gh workflow run deploy.yml -f sha="$(git rev-parse origin/main)"
```

cloud-init reattacha el volumen sin formatearlo. Si el volumen tambien se perdio, restaura el ultimo dump del bucket con `restore_backup.sh` (paso 9).
