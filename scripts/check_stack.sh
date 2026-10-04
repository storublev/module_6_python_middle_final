#!/usr/bin/env bash
# Живая проверка поднятого стека кинотеатра: документация сервисов, вход,
# путь уведомления до письма в почтовом ящике и бронирование билетов — показ,
# бронь последнего места, отказ лишнему, страницы интерфейса.
#
# Зачем скриптом: после `docker compose up -d` хочется одной командой увидеть,
# что работает не только «контейнер запущен», но и сквозной сценарий.
#
#   docker compose up -d
#   ./scripts/check_stack.sh
#   BASE=http://localhost:8080 ./scripts/check_stack.sh   # если nginx на другом порту
#
# Скрипт ничего не удаляет: он заводит своих зрителей и свой показ со
# случайными именами, а показ в конце отменяет, поэтому его можно запускать
# на живом стенде.
set -uo pipefail

BASE=${BASE:-http://localhost}
LOGIN="check-$RANDOM@example.com"
PASSWORD='Str0ng-Passw0rd!'

FAILED=0
ok() { printf '  ✅ %s\n' "$1"; }
bad() { printf '  ❌ %s\n' "$1"; FAILED=1; }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
json_field() { python3 -c "import sys, json; print(json.load(sys.stdin).get('$1', ''))" 2>/dev/null; }

echo '1. Документация сервисов'
for path in /api/openapi /auth/api/openapi /notify/api/openapi /booking/api/openapi; do
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

echo '3. Уведомления'
# Служебный секрет нужен и сервисам, и этому скрипту: событие присылает не
# пользователь, а другая часть системы.
SERVICE_TOKEN=${AUTH_SERVICE_TOKEN:-}
if [ -z "$SERVICE_TOKEN" ]; then
    bad 'AUTH_SERVICE_TOKEN не задан — проверить уведомления нечем'
else
    SERVICE=(-H "X-Service-Token: $SERVICE_TOKEN" -H 'Content-Type: application/json')
    status=$(code "$BASE/notify/api/v1/health")
    [ "$status" = 200 ] && ok "/notify/api/v1/health → $status" || bad "/notify/api/v1/health → $status"

    # Зрителю нужны контакты: без адреса письмо собрать не из чего.
    #
    # Часовой пояс подбирается так, чтобы у зрителя сейчас был полдень.
    # Сервис не пишет ночью (окно тишины 21:00–09:00 по времени получателя),
    # и с фиксированным поясом эта проверка падала бы каждую ночь — не
    # потому, что стенд сломан, а потому, что он работает правильно.
    OFFSET=$(( (12 - $(date -u +%-H) + 24) % 24 ))
    [ "$OFFSET" -gt 12 ] && OFFSET=$(( OFFSET - 24 ))
    # В именах Etc/GMT знак обратный: Etc/GMT-3 — это UTC+3.
    if [ "$OFFSET" -ge 0 ]; then VIEWER_TZ="Etc/GMT-$OFFSET"; else VIEWER_TZ="Etc/GMT+$(( -OFFSET ))"; fi
    profile=$(curl -s -X PATCH "$BASE/auth/api/v1/users/me/profile" "${AUTH[@]}" \
        -d "{\"email\": \"$LOGIN\", \"first_name\": \"Проверка\", \"timezone\": \"$VIEWER_TZ\"}")
    printf '%s' "$profile" | grep -q "$LOGIN" && ok 'контакты зрителя заполнены' || bad "контакты: $profile"

    EVENT_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')
    USER_ID=$(curl -s "$BASE/auth/api/v1/users/me" "${AUTH[@]}" | json_field id)
    event=$(curl -s -X POST "$BASE/notify/api/v1/events" "${SERVICE[@]}" -d "{
        \"event_id\": \"$EVENT_ID\",
        \"routing_key\": \"film-reporting.v1.episode-added\",
        \"template_code\": \"new_episode\",
        \"audience\": {\"kind\": \"users\", \"user_ids\": [\"$USER_ID\"]},
        \"context\": {\"film_title\": \"Проверка стенда\", \"episode\": 8}
    }")
    printf '%s' "$event" | grep -q '"accepted":true' && ok 'событие принято' || bad "событие: $event"

    # Повтор с тем же event_id не должен создать второго письма.
    repeat=$(curl -s -X POST "$BASE/notify/api/v1/events" "${SERVICE[@]}" -d "{
        \"event_id\": \"$EVENT_ID\",
        \"routing_key\": \"film-reporting.v1.episode-added\",
        \"template_code\": \"new_episode\",
        \"audience\": {\"kind\": \"users\", \"user_ids\": [\"$USER_ID\"]}
    }")
    printf '%s' "$repeat" | grep -q '"accepted":false' \
        && ok 'повтор события отбит' || bad "повтор: $repeat"

    # Письмо проходит три очереди и трёх воркеров — ждём его в Mailpit.
    MAILPIT=${MAILPIT_URL:-http://localhost:${MAILPIT_UI_PORT:-8025}}
    delivered=0
    for _ in $(seq 1 30); do
        found=$(curl -s "$MAILPIT/api/v1/search?query=to:$LOGIN" | json_field total)
        [ "${found:-0}" -ge 1 ] 2>/dev/null && delivered=1 && break
        sleep 1
    done
    [ "$delivered" = 1 ] && ok "письмо доставлено (см. $MAILPIT)" \
        || bad "письмо не дошло за 30 с (часовой пояс проверки: $VIEWER_TZ)"

    # Короткая ссылка: сокращаем и проверяем перенаправление.
    link=$(curl -s -X POST "$BASE/notify/api/v1/links" "${SERVICE[@]}" \
        -d '{"target_url": "http://localhost/api/openapi"}')
    KEY=$(printf '%s' "$link" | json_field key)
    if [ -n "$KEY" ]; then
        status=$(code -o /dev/null "$BASE/s/$KEY")
        [ "$status" = 302 ] && ok 'короткая ссылка ведёт на адрес (302)' || bad "короткая ссылка: $status"
    else
        bad "короткая ссылка: $link"
    fi

    notifications=$(curl -s "$BASE/notify/api/v1/me/notifications" -H "Authorization: Bearer $TOKEN")
    printf '%s' "$notifications" | grep -q '"total"' \
        && ok 'уведомления видны в личном кабинете' || bad "личный кабинет: $notifications"
fi

echo '4. Бронирование билетов (диплом)'
# Фильм — самый рейтинговый полнометражный из каталога: только на такие
# можно создать показ. Хост — зритель проверки, гость — второй зритель.
MOVIE=$(curl -s "$BASE/api/v1/films?type=movie&page_size=1" \
    | python3 -c 'import sys, json; print(json.load(sys.stdin)[0]["uuid"])' 2>/dev/null)
[ -n "$MOVIE" ] && ok "полнометражный фильм из каталога: $MOVIE" || bad 'каталог не отдал фильм с type=movie'
STARTS=$(python3 -c 'import datetime as d; print((d.datetime.now(d.timezone.utc) + d.timedelta(days=3)).isoformat())')
screening=$(curl -s -X POST "$BASE/booking/api/v1/screenings" "${AUTH[@]}" \
    -d "{\"film_id\": \"$MOVIE\", \"starts_at\": \"$STARTS\", \"place\": \"Зал проверки\", \"address\": \"Арбат, 1\", \"capacity\": 1}")
SCREENING=$(printf '%s' "$screening" | json_field id)
[ -n "$SCREENING" ] && ok "показ на одно место создан: $SCREENING" || bad "показ: $screening"

GUEST="guest-$RANDOM@example.com"
curl -s -o /dev/null -X POST "$BASE/auth/api/v1/signup" -H 'Content-Type: application/json' \
    -d "{\"login\": \"$GUEST\", \"password\": \"$PASSWORD\"}"
GUEST_TOKEN=$(curl -s -X POST "$BASE/auth/api/v1/login" -H 'Content-Type: application/json' \
    -d "{\"login\": \"$GUEST\", \"password\": \"$PASSWORD\"}" | json_field access_token)
GUEST_AUTH=(-H "Authorization: Bearer $GUEST_TOKEN" -H 'Content-Type: application/json')
booking=$(curl -s -X POST "$BASE/booking/api/v1/screenings/$SCREENING/bookings" "${GUEST_AUTH[@]}" -d '{"seats": 1}')
BOOKING=$(printf '%s' "$booking" | json_field id)
[ -n "$BOOKING" ] && ok 'гость забронировал последнее место' || bad "бронь гостя: $booking"
# Мест у хоста одно, и оно уже занято: второе место не продаётся.
overbook=$(curl -s -X PATCH "$BASE/booking/api/v1/bookings/$BOOKING" "${GUEST_AUTH[@]}" -d '{"seats": 2}')
printf '%s' "$overbook" | grep -q 'not_enough_seats' \
    && ok 'лишнее место не продаётся (409 not_enough_seats)' || bad "перебронирование: $overbook"
hosts=$(curl -s "$BASE/booking/api/v1/films/$MOVIE/hosts?page_size=100")
printf '%s' "$hosts" | grep -q '"host_id"' && ok 'хосты фильма видны' || bad "хосты фильма: $hosts"
for path in / "/films/$MOVIE" "/screenings/$SCREENING" /afisha; do
    status=$(code "$BASE$path")
    [ "$status" = 200 ] && ok "страница $path → $status" || bad "страница $path → $status"
done
page=$(curl -s "$BASE/films/$MOVIE")
printf '%s' "$page" | grep -q 'Купить билет' && ok 'в карточке фильма есть «Купить билет»' || bad 'нет кнопки «Купить билет»'
# Показ проверки отменяется: иначе каждый прогон оставлял бы в карточке
# самого популярного фильма ещё одного хоста «Проверка» без мест.
status=$(code -X POST "$BASE/booking/api/v1/screenings/$SCREENING/cancel" "${AUTH[@]}")
[ "$status" = 200 ] && ok 'показ проверки отменён и убран из карточки фильма' || bad "отмена показа: $status"

echo
[ "$FAILED" = 0 ] && echo 'ИТОГ: стенд работает' || echo 'ИТОГ: есть ошибки'
echo "зритель проверки: $LOGIN"
exit "$FAILED"
