# Publicar DataPick gratis en Oracle Cloud

Oracle Cloud Always Free incluye un servidor ARM (hoy 2 núcleos y 12 GB de RAM) que no caduca.
Pide una tarjeta para verificar la identidad, pero no cobra mientras la cuenta siga en el plan gratuito.

## 1. Crear la cuenta (10 minutos)

1. Entra en https://www.oracle.com/cloud/free/ y pulsa **Start for free**.
2. Elige la **Home Region** con cuidado: no se puede cambiar después. Para Latinoamérica sirven
   *Brazil East (São Paulo)*, *Chile Central (Santiago)* o *US East (Ashburn)*.
3. Verifica el correo y la tarjeta. Puede aparecer un cargo temporal de verificación que se devuelve.
4. No actives "Upgrade to Pay As You Go" salvo que quieras: el plan gratuito es suficiente.

## 2. Crear el servidor

1. Menú **Compute > Instances > Create instance**. Nombre: `datapick`.
2. **Image**: *Canonical Ubuntu 24.04*.
3. **Shape**: *Change shape > Ampere > VM.Standard.A1.Flex*, con **2 OCPU** y **12 GB**.
   Si aparece *Out of capacity*, prueba otro *Availability domain*, baja a 1 OCPU y 6 GB, o inténtalo más tarde.
4. **Networking**: deja que cree una red nueva con subred pública y marca *Assign a public IPv4 address*.
5. **Add SSH keys**: *Generate a key pair for me* y pulsa **Save private key**. Guarda ese archivo `.key`.
6. **Create**. Cuando el estado sea *Running*, copia la **Public IP address**.

## 3. Abrir los puertos web

1. En la página del servidor, entra en la **Subnet** y luego en su **Security List** (*Default Security List*).
2. **Add Ingress Rules**, dos veces:
   - Source CIDR `0.0.0.0/0`, IP Protocol `TCP`, Destination Port `80`
   - Source CIDR `0.0.0.0/0`, IP Protocol `TCP`, Destination Port `443`

## 4. Desplegar (lo hace Claude o tú, desde Git Bash en el PC)

```bash
cd pronosticos
bash deploy/deploy.sh ubuntu@IP_PUBLICA "C:/Users/TU_USUARIO/Downloads/ssh-key-XXXX.key"
```

Al terminar muestra:

- la API: `https://IP-CON-GUIONES.sslip.io`
- la descarga del APK: `https://IP-CON-GUIONES.sslip.io/descargar/`

`sslip.io` es un servicio gratuito que convierte la IP en un nombre de dominio, lo que permite HTTPS
sin comprar dominio. Si tienes uno propio: `DOMAIN=midominio.com bash deploy/deploy.sh ...`

## 5. Mantenimiento

- Ver el motor: `ssh -i ~/.ssh/datapick_oracle.key ubuntu@IP 'cd datapick && sudo docker compose -f docker-compose.prod.yml logs -f worker'`
- Actualizar tras cambios: volver a ejecutar `deploy/deploy.sh` (conserva datos y claves).
- Las claves quedan en `~/datapick/.env.prod` en el servidor. Guarda una copia.

## Riesgos conocidos del plan gratuito

- Oracle puede **recuperar servidores gratuitos inactivos** (uso de CPU, red y memoria muy bajo durante 7 días).
  DataPick usa poca CPU, así que es posible. Pasar la cuenta a *Pay As You Go* lo evita y, sin salir de los
  límites gratuitos, sigue costando 0, pero ya admite cargos si se crean recursos de pago.
- La capacidad ARM gratuita no está garantizada en todas las regiones.
- ESPN podría limitar peticiones desde centros de datos. Si pasa, se verá en el registro del worker.
