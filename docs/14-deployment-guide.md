# Single-server Deployment

The MVP deployment target is one Linux server running Docker Compose. Only the `gateway`
service publishes network ports. PostgreSQL, Redis, backend, and worker remain on the internal
Compose network.

## Prepare configuration

```bash
cp .env.example .env
```

Set at minimum:

- `DOMAIN`
- `POSTGRES_PASSWORD`
- `DATABASE_URL` with the same database password
- `DATA_ROOT` on a persistent disk
- `MODEL_DATA_ROOT` on a persistent disk

## Validate and deploy

```bash
docker compose -f compose.yaml -f compose.production.yaml config
docker compose -f compose.yaml -f compose.production.yaml build
docker compose -f compose.yaml -f compose.production.yaml up -d postgres redis
docker compose -f compose.yaml -f compose.production.yaml --profile tools run --rm migrate
docker compose -f compose.yaml -f compose.production.yaml up -d
```

Verify:

```bash
docker compose -f compose.yaml -f compose.production.yaml ps
curl https://YOUR_DOMAIN/api/v1/health
```

Back up both PostgreSQL and `DATA_ROOT`. Redis is queue infrastructure and is not the source of
truth. Do not expose PostgreSQL or Redis ports publicly.

