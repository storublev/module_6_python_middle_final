#!/bin/sh
# Ждём зависимости и запускаем тесты; аргументы контейнера уходят в pytest.
set -e

python -m tests.functional.utils.wait_for_services

exec pytest -p no:cacheprovider tests/functional "$@"
