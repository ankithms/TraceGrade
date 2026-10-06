.PHONY: up down logs test-backend check

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs --follow

test-backend:
	cd backend && pytest

check:
	cd backend && ruff check app tests && pytest
