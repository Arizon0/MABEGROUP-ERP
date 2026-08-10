.PHONY: ajuda instalar dev api worker frontend testes migrar migration docker limpar

ajuda:
	@echo "Marketplace Hub — comandos disponíveis"
	@echo ""
	@echo "  make instalar   Instala dependências de backend e frontend"
	@echo "  make docker     Sobe a plataforma inteira (Postgres + Redis + API + worker + painel)"
	@echo "  make api        Sobe só a API em modo simulado"
	@echo "  make worker     Sobe o worker de sincronização"
	@echo "  make frontend   Sobe o painel"
	@echo "  make testes     Roda a suíte completa (backend + frontend)"
	@echo "  make migrar     Aplica as migrations"
	@echo "  make migration  Gera uma migration (m=\"descrição\")"

instalar:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
	cd frontend && npm install

api:
	cd backend && USE_MOCK_CONNECTORS=1 .venv/bin/uvicorn app.main:app --reload --port 8000

worker:
	cd backend && USE_MOCK_CONNECTORS=1 .venv/bin/arq app.workers.settings.WorkerSettings

frontend:
	cd frontend && npm run dev

testes:
	cd backend && .venv/bin/python -m pytest -q
	cd frontend && npx tsc --noEmit && npx vitest run

migrar:
	cd backend && .venv/bin/alembic upgrade head

migration:
	cd backend && .venv/bin/alembic revision --autogenerate -m "$(m)"

docker:
	docker compose up --build

limpar:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf backend/.pytest_cache frontend/dist backend/*.db
