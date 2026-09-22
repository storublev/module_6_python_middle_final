# Полный прогон исследования: поднять хранилища, замерить оба, собрать отчёт.
#
#   make -C ugc_content/research all
#
# Прогон долгий (десять миллионов записей в каждое хранилище), поэтому шаги
# можно запускать по отдельности: up, mongodb, postgresql, down.

COMPOSE = docker compose -f docker-compose.yml
PYTHON ?= ../../.venv/bin/python
ROWS ?= 10000000
REPEAT ?= 200
WORKERS ?= 4

.PHONY: all up down mongodb postgresql clean

all: up mongodb postgresql

up:
	$(COMPOSE) up -d
	@echo 'Ждём готовности хранилищ…'
	@until $(COMPOSE) ps --format '{{.Health}}' | grep -q healthy; do sleep 2; done

mongodb:
	$(PYTHON) bench.py all --storage mongodb --rows $(ROWS) --repeat $(REPEAT) \
		--workers $(WORKERS) --report results-mongodb.json

postgresql:
	$(PYTHON) bench.py all --storage postgresql --rows $(ROWS) --repeat $(REPEAT) \
		--workers $(WORKERS) --report results-postgresql.json

down:
	$(COMPOSE) down

# Тома с данными исследования занимают несколько гигабайт: после прогона их
# имеет смысл убрать.
clean:
	$(COMPOSE) down -v
