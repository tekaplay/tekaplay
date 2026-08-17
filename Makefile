.PHONY: up down logs test lint fmt migrate revision check-env backup

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f api worker

test:
	cd backend && pytest -q

lint:
	cd backend && ruff check app tests && mypy app
	cd frontend && npm run lint

fmt:
	cd backend && ruff format app tests

migrate:
	cd backend && alembic upgrade head

revision:
	cd backend && alembic revision --autogenerate -m "$(m)"

# Validate configuration without deploying it. Prints what the application
# resolved, with credentials redacted, and warns about no-op integrations.
check-env:
	cd backend && python -m app.scripts.check_env

# Portable pg_dump archive of $$DATABASE_URL. See docs/RUNBOOK.md §7.
backup:
	./scripts/backup.sh
