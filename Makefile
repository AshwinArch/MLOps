.PHONY: install test test-backend test-pg test-frontend lint build run up
install:
	cd backend && pip install -r requirements-dev.txt
	cd frontend && npm ci
test: test-backend test-frontend
test-backend:
	cd backend && python -m pytest --cov=app --cov-fail-under=85
test-pg:  ## needs a PostgreSQL: TEST_DATABASE_URL=postgresql://mlops:mlops@localhost:5432/mlops_test
	cd backend && python -m pytest
test-frontend:
	cd frontend && npx ng test --watch=false --browsers=ChromeHeadlessCI
lint:
	cd backend && ruff check . && lint-imports
build:
	cd frontend && npx ng build
run:  ## backend on :8000 (local, SQLite, demo switches ON) - `cd frontend && npm start` for the UI on :4200
	cd backend && DEFAULT_ROLE=admin SEED_ON_STARTUP=true ENABLE_FAILURE_SIMULATION=true REQUIRE_FOUR_EYES=false REQUIRE_ARTIFACT_CHECKSUM=false uvicorn app.main:get_app --factory --reload --port 8000
up:
	docker compose up --build
