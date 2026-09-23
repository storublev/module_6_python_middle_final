#!/usr/bin/env bash
# Собирает шардированный кластер MongoDB из контейнеров docker-compose.cluster.yml.
#
#   docker compose -f ugc_content/research/docker-compose.cluster.yml up -d
#   ./ugc_content/research/init_cluster.sh
#
# Поднятые контейнеры друг о друге ещё не знают: узлы нужно собрать в наборы
# реплик, наборы — представить маршрутизаторам, а коллекции — шардировать.
# Скрипт идемпотентен: повторный запуск на собранном кластере ничего не ломает,
# rs.initiate и addShard на уже настроенном кластере отвечают ошибкой, которую
# мы проглатываем.
set -euo pipefail

DB=${RESEARCH_MONGO_DB:-research}

run() {
    local container=$1 script=$2
    docker exec "$container" mongosh --quiet --eval "$script" || true
}

echo '1/4 Серверы конфигурации'
run mongocfg1 'rs.initiate({_id: "mongocfg", configsvr: true, members: [
    {_id: 0, host: "mongocfg1:27017"},
    {_id: 1, host: "mongocfg2:27017"},
    {_id: 2, host: "mongocfg3:27017"}]})'

echo '2/4 Наборы реплик шардов'
run mongors1n1 'rs.initiate({_id: "mongors1", members: [
    {_id: 0, host: "mongors1n1:27017"},
    {_id: 1, host: "mongors1n2:27017"},
    {_id: 2, host: "mongors1n3:27017"}]})'
run mongors2n1 'rs.initiate({_id: "mongors2", members: [
    {_id: 0, host: "mongors2n1:27017"},
    {_id: 1, host: "mongors2n2:27017"},
    {_id: 2, host: "mongors2n3:27017"}]})'

echo '3/4 Шарды в кластере'
# Маршрутизатор поднимается раньше, чем наборы реплик выбирают первичный узел.
sleep 10
run mongos1 'sh.addShard("mongors1/mongors1n1:27017")'
run mongos1 'sh.addShard("mongors2/mongors2n1:27017")'

echo '4/4 Шардирование коллекций'
# Ключ шардирования — хешированный идентификатор фильма для оценок и
# зрителя для закладок: запросы карточки идут по фильму, запросы личных
# списков — по зрителю, и каждый должен попадать в один шард, а не во все.
run mongos1 "sh.enableSharding('$DB')"
run mongos1 "sh.shardCollection('$DB.likes', {film_id: 'hashed'})"
run mongos1 "sh.shardCollection('$DB.film_ratings', {film_id: 'hashed'})"
run mongos1 "sh.shardCollection('$DB.bookmarks', {user_id: 'hashed'})"
run mongos1 "sh.shardCollection('$DB.reviews', {film_id: 'hashed'})"

echo 'Готово. Состояние кластера:'
docker exec mongos1 mongosh --quiet --eval 'sh.status()' | head -40
