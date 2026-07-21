.PHONY: config build up down logs migrate test smoke schema-check

config:
	docker compose config

build:
	docker compose build

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

migrate:
	docker compose --profile tools run --rm migrate

test:
	docker compose --profile tools run --rm --no-deps test

smoke:
	docker compose exec backend python -m scripts.smoke_worker

schema-check:
	docker compose exec backend python -m scripts.verify_database_schema
