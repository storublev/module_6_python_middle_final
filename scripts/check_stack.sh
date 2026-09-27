#!/usr/bin/env bash
# Живая проверка поднятого стека кинотеатра: документация сервисов, полный
# путь зрителя через сервис пользовательского контента и приём события
# сервисом сбора действий.
#
# Зачем скриптом: после `docker compose up -d` хочется одной командой увидеть,
# что работает не только «контейнер запущен», но и сквозной сценарий —
# регистрация, оценка фильма, агрегат, рецензия, закладка и событие.
#
#   docker compose up -d
#   ./scripts/check_stack.sh
#   BASE=http://localhost:8080 ./scripts/check_stack.sh   # если nginx на другом порту
#
# Скрипт ничего не удаляет: он заводит своего зрителя и свой фильм со
# случайными идентификаторами, поэтому его можно запускать на живом стенде.
set -uo pipefail

BASE=${BASE:-http://localhost}
LOGIN="check-$RANDOM@example.com"
PASSWORD='Str0ng-Passw0rd!'
FILM=$(python3 -c 'import uuid; print(uuid.uuid4())')

FAILED=0
ok() { printf '  ✅ %s\n' "$1"; }
bad() { printf '  ❌ %s\n' "$1"; FAILED=1; }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
json_field() { python3 -c "import sys, json; print(json.load(sys.stdin).get('$1', ''))" 2>/dev/null; }

echo '1. Документация сервисов'
for path in /api/openapi /auth/api/openapi /ugc/api/openapi /content/api/openapi; do
    status=$(code "$BASE$path")
    [ "$status" = 200 ] && ok "$path → $status" || bad "$path → $status"
done
status=$(code "$BASE/admin/")
# Админка без входа отвечает перенаправлением на страницу входа.
{ [ "$status" = 302 ] || [ "$status" = 200 ]; } && ok "/admin/ → $status" || bad "/admin/ → $status"

echo '2. Регистрация и вход'
signup=$(curl -s -X POST "$BASE/auth/api/v1/signup" -H 'Content-Type: application/json' \
    -d "{\"login\": \"$LOGIN\", \"password\": \"$PASSWORD\"}")
printf '%s' "$signup" | grep -q '"id"' && ok 'учётная запись создана' || bad "регистрация: $signup"
login=$(curl -s -X POST "$BASE/auth/api/v1/login" -H 'Content-Type: application/json' \
    -d "{\"login\": \"$LOGIN\", \"password\": \"$PASSWORD\"}")
TOKEN=$(printf '%s' "$login" | json_field access_token)
[ -n "$TOKEN" ] && ok 'получен access-токен' || { bad "токен не получен: $login"; exit 1; }
AUTH=(-H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json')

echo "3. Пользовательский контент (фильм $FILM)"
rating=$(curl -s -X PUT "$BASE/content/api/v1/films/$FILM/rating" "${AUTH[@]}" -d '{"rating": 9}')
printf '%s' "$rating" | grep -q '"rating":9' && ok 'оценка поставлена' || bad "оценка: $rating"

aggregate=$(curl -s "$BASE/content/api/v1/films/$FILM/rating")
printf '%s' "$aggregate" | grep -q '"likes":1' \
    && ok "агрегат виден сразу: $aggregate" || bad "агрегат: $aggregate"

bookmark=$(curl -s -X PUT "$BASE/content/api/v1/films/$FILM/bookmark" "${AUTH[@]}")
printf '%s' "$bookmark" | grep -q "$FILM" && ok 'фильм в закладках' || bad "закладка: $bookmark"

review=$(curl -s -X POST "$BASE/content/api/v1/films/$FILM/reviews" "${AUTH[@]}" \
    -d '{"text": "Проверочная рецензия живого стенда.", "rating": 9}')
REVIEW_ID=$(printf '%s' "$review" | json_field review_id)
[ -n "$REVIEW_ID" ] && ok 'рецензия опубликована' || bad "рецензия: $review"

if [ -n "$REVIEW_ID" ]; then
    # Голосует второй зритель: за свою рецензию голосовать нельзя, иначе автор
    # накручивал бы себе полезность.
    own=$(curl -s -X PUT "$BASE/content/api/v1/reviews/$REVIEW_ID/vote" "${AUTH[@]}" -d '{"useful": true}')
    printf '%s' "$own" | grep -q 'own_review_vote' \
        && ok 'голос автора за свою рецензию отклонён' || bad "самооценка: $own"

    READER_LOGIN="reader-$RANDOM@example.com"
    curl -s -o /dev/null -X POST "$BASE/auth/api/v1/signup" -H 'Content-Type: application/json' \
        -d "{\"login\": \"$READER_LOGIN\", \"password\": \"$PASSWORD\"}"
    reader_login=$(curl -s -X POST "$BASE/auth/api/v1/login" -H 'Content-Type: application/json' \
        -d "{\"login\": \"$READER_LOGIN\", \"password\": \"$PASSWORD\"}")
    READER_TOKEN=$(printf '%s' "$reader_login" | json_field access_token)
    READER=(-H "Authorization: Bearer $READER_TOKEN" -H 'Content-Type: application/json')

    vote=$(curl -s -X PUT "$BASE/content/api/v1/reviews/$REVIEW_ID/vote" "${READER[@]}" -d '{"useful": true}')
    printf '%s' "$vote" | grep -q '"useful":1' && ok 'голос читателя учтён' || bad "голос: $vote"
    list=$(curl -s "$BASE/content/api/v1/films/$FILM/reviews?sort=most_useful")
    printf '%s' "$list" | grep -q '"total":1' \
        && ok 'рецензия видна в списке без токена' || bad "список рецензий: $list"
fi

mine=$(curl -s "$BASE/content/api/v1/users/me/likes" -H "Authorization: Bearer $TOKEN")
printf '%s' "$mine" | grep -q "$FILM" && ok 'фильм в понравившихся' || bad "понравившиеся: $mine"

status=$(code -X PUT "$BASE/content/api/v1/films/$FILM/rating" \
    -H 'Content-Type: application/json' -d '{"rating": 1}')
[ "$status" = 401 ] && ok 'без токена запись отклонена (401)' || bad "без токена: $status"

echo '4. Сбор пользовательских действий'
EVENT=$(python3 - "$FILM" <<'PY'
import json
import sys
import uuid
from datetime import datetime, timezone

print(json.dumps({'events': [{
    'event_id': str(uuid.uuid4()),
    'session_id': str(uuid.uuid4()),
    'event_type': 'video_completed',
    'occurred_at': datetime.now(timezone.utc).isoformat(),
    'client': {'platform': 'web'},
    'film_id': sys.argv[1],
    'watched_ratio': 0.97,
    'duration_ms': 5400000,
}]}))
PY
)
status=$(code -X POST "$BASE/ugc/api/v1/events" "${AUTH[@]}" -d "$EVENT")
[ "$status" = 202 ] && ok 'событие принято (202)' || bad "событие: $status"

echo
[ "$FAILED" = 0 ] && echo 'ИТОГ: стенд работает' || echo 'ИТОГ: есть ошибки'
echo "фильм проверки: $FILM, зритель: $LOGIN"
exit "$FAILED"
