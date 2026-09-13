#!/bin/sh
# Ждём хранилища и запускаем тесты; аргументы контейнера уходят в pytest.
set -e

python -m tests.functional.utils.wait_for_es
python -m tests.functional.utils.wait_for_redis

exec pytest -p no:cacheprovider tests/functional "$@"
