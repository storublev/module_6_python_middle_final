#!/bin/sh
# Ждём MongoDB и сервис и запускаем тесты; аргументы контейнера уходят в pytest.
set -e

python -m tests.functional.utils.wait_for_services

exec pytest -p no:cacheprovider tests/functional "$@"
