# DataPick API

Backend de pronósticos de fútbol basados en datos. Importa el calendario, los resultados y las cuotas
reales de las 5 grandes ligas y la Champions, calcula probabilidades con un modelo Poisson mezclado con
el mercado, y publica picks solo cuando cumplen las reglas y tienen valor esperado positivo.

| Componente | Tecnología |
|---|---|
| API | Python 3.12, FastAPI, asyncpg |
| Base de datos | PostgreSQL 16 con TimescaleDB |
| Worker | Proceso que sincroniza datos y ejecuta el motor cada 10 minutos |
| HTTPS en producción | Caddy con certificados automáticos |

## Reglas de negocio

- **Pick Segura**: probabilidad de 80 % o más, con valor esperado positivo.
- **Pick Favorito**: probabilidad entre 70 % y 79 %, cuota mínima 1.50 y valor esperado positivo.
- **Combinadas**: cuota total entre 5.00 y 7.00, máximo un leg por partido.
- **Stake**: Kelly fraccional ¼, con tope de 3 % por pick y 1 % por combinada.
- Los picks quedan en un registro inmutable con hash encadenado para el panel de transparencia.

## Ejecutar en local

```bash
docker compose up -d --build
```

- API: http://localhost:8000 · documentación interactiva: http://localhost:8000/docs
- Registro del motor: `docker logs -f pronosticos-worker`
- Tests: `docker compose exec api sh -c "pip install -r requirements-dev.txt && pytest"`

El primer arranque importa los resultados de la temporada, lo que tarda uno o dos minutos.

## Desplegar en un servidor

Sirve cualquier servidor Linux con Ubuntu 22.04 o 24.04, ARM o x86, con al menos 2 GB de RAM.
La guía para hacerlo gratis en Oracle Cloud está en [deploy/ORACLE.md](deploy/ORACLE.md).

En el servidor, con los puertos 80 y 443 abiertos:

```bash
git clone https://github.com/TU_USUARIO/datapick-api.git datapick
cd datapick
bash deploy/setup.sh
```

El script instala Docker, abre los puertos del sistema, genera `.env.prod` con claves aleatorias y
arranca todo. Al terminar muestra la dirección HTTPS de la API, del tipo `https://1-2-3-4.sslip.io`.
Para usar un dominio propio: `DOMAIN=api.midominio.com bash deploy/setup.sh`.

Para actualizar después de cambios en el repositorio:

```bash
cd datapick && git pull && sudo docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

En la app, toca el indicador "Datos reales / Demo" de la cabecera y escribe la dirección del servidor.

> Railway, Render y otros servicios gestionados no suelen incluir TimescaleDB en su PostgreSQL,
> y el esquema lo necesita. Lo más sencillo es un servidor virtual con Docker.

## Endpoints principales

| Método | Ruta | Uso |
|---|---|---|
| POST | `/auth/device` | Registra un teléfono (mayor de 18) y devuelve su token |
| GET | `/picks/active` | Picks vigentes con historial de cuota y motivos |
| GET | `/picks/{id}` | Detalle de un pick |
| GET | `/matches/week` | Calendario de la semana con cuotas, 1X2 y picks |
| GET | `/parlays/today` | Combinadas publicadas |
| GET | `/transparency` | Yield, win rate, CLV, calibración e integridad del registro |
| GET | `/me/bankroll` · PUT `/me/risk` | Bankroll y ajustes de Kelly del usuario |
| GET | `/me/alerts` | Notificaciones del usuario |
| POST | `/admin/...` | Operaciones internas, con cabecera `X-Admin-Key` |

Las rutas `/me` requieren `Authorization: Bearer <token>`.

## Estructura

```
api/            código de la API, el worker, el modelo y los tests
  app/etl/      importadores: ESPN (sin clave) y API-Football (con clave)
  app/model/    modelo Poisson
  app/pipeline.py  predicciones, publicación, liquidación
db/             esquema y datos iniciales (se aplican al crear la base)
deploy/         instalación en servidor, HTTPS y guía de Oracle Cloud
release/        página de descarga del APK (el .apk se copia aparte)
```

## Avisos

- ESPN es una API pública no oficial: útil para empezar, pero puede cambiar sin aviso.
  Para producción conviene un proveedor con contrato.
- Las claves de `docker-compose.yml` son solo de desarrollo. En producción las genera `setup.sh`.
- Los pronósticos no garantizan ganancias. La app exige mayoría de edad.
