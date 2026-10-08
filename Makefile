.PHONY: up down logs test-backend test-sdk check

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs --follow

test-backend:
	cd backend && pytest

test-sdk:
	cd sdk && pytest

check:
	cd backend && ruff check app tests && pytest
	cd sdk && ruff check src tests examples && pytest
