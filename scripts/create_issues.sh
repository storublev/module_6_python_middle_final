#!/usr/bin/env bash
# Заводит на доске GitHub задачи восьмого спринта по docs/planning.md.
#
# Зачем скриптом: задач под тридцать, у каждой оценка, роль и связи; руками
# они заводятся полчаса, а при переносе в другой репозиторий — ещё раз.
#
# Требуется GitHub CLI (`gh auth login`). Запуск из корня репозитория:
#   ./scripts/create_issues.sh                # завести задачи
#   ./scripts/create_issues.sh --dry-run      # только показать, что будет заведено
set -euo pipefail

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

if ! command -v gh >/dev/null 2>&1; then
    echo 'Нужен GitHub CLI: brew install gh && gh auth login' >&2
    exit 1
fi

# Метки оценок и ролей: по ним доска фильтруется, а сумма оценок колонки
# показывает, сколько работы в ней осталось.
LABELS=(
    'epic:6f42c1:Эпик — пункт задания модуля'
    'blocking:b60205:Блокирующая задача: до её ревью реализация не начинается'
    'sp-1:c2e0c6:Оценка 1 story point'
    'sp-2:c2e0c6:Оценка 2 story points'
    'sp-3:fef2c0:Оценка 3 story points'
    'sp-5:f9d0c4:Оценка 5 story points'
    'role-architect:0e8a16:Роль: архитектор'
    'role-backend:1d76db:Роль: backend UGC'
    'role-data:5319e7:Роль: данные и хранилища'
    'role-etl:006b75:Роль: ETL'
    'role-qa:d93f0b:Роль: QA и DevOps'
)

# Задача — строка «эпик|заголовок|оценка|роль|зависимости». Порядок тот же,
# что в docs/planning.md: заведённые задачи ложатся на доску сверху вниз.
ISSUES=(
    'E1|Договориться о шкале оценок и определении Done|1|role-architect|—'
    'E1|Декомпозировать задание модуля на задачи и эпики|2|role-architect|1'
    'E1|Завести задачи в GitHub, расставить оценки и метки|2|role-qa|2'
    'E2|Выписать функциональные требования из описания задачи маркетинга|2|role-architect|2'
    'E2|Выписать нефункциональные требования (доступность, задержки, хранение)|3|role-architect|4'
    'E2|Посчитать метрики нагрузки: DAU, MAU, RPS, размер среднего сообщения|3|role-data|4'
    'E3|Нарисовать схему AS IS в нотации C4 (уровень 2)|3|role-architect|2'
    'E3|Нарисовать схему TO BE с выделением новых и изменённых элементов|3|role-architect|7'
    'E3|Записать ADR: путь сбора событий, выбор брокера, выбор фреймворка|2|role-architect|8'
    'E4|Поднять Kafka в KRaft-режиме в общем compose, завести топики|3|role-data|8'
    'E4|Каркас сервиса на Flask + gevent: настройки, журнал, слои|3|role-backend|8'
    'E4|Контракт событий: клики, просмотры страниц, кастомные события|3|role-backend|4'
    'E4|Аутентификация по access-токену сервиса авторизации|3|role-backend|11'
    'E4|Отправка событий в Kafka пачками, ключ партиционирования|3|role-backend|10, 12'
    'E4|Маршрут /ugc/ в nginx, документация OpenAPI, README сервиса|2|role-qa|11'
    'E4|Unit- и функциональные тесты сервиса сбора событий|5|role-qa|14, 15'
    'E5|Спроектировать схему хранения событий под аналитические сценарии|3|role-data|6'
    'E5|Поднять ClickHouse и Vertica, подготовить окружение исследования|3|role-data|17'
    'E5|Сгенерировать не менее 10 000 000 событий, залить пачками|3|role-data|18'
    'E5|Замерить скорость вставки и чтения на загруженных данных|3|role-data|19'
    'E5|Замерить то же под нагрузкой: чтение при непрерывной записи|3|role-data|20'
    'E5|Свести результаты в README и выбрать хранилище (ADR)|1|role-data|21'
    'E6|Схема аналитических таблиц: просматриваемые и недосмотренные фильмы|3|role-data|17'
    'E6|Чтение из Kafka группой потребителей, коммит смещений после записи|3|role-etl|14, 23'
    'E6|Вставка в ClickHouse пачками с повторами и прерывателем|3|role-etl|24'
    'E6|Устойчивость к сбоям источника и хранилища, проверка на живом стеке|3|role-etl|25'
    'E6|Мониторинг памяти приложения и защита от роста потребления|3|role-etl|25'
    'E6|Тесты ETL и раздел в README|3|role-qa|26, 27'
)

# Эпики блокирующие — E2 и E3: к реализации переходим после их ревью.
declare -a EPICS=(
    'E1|Планирование работы команды|'
    'E2|Выявление требований|blocking'
    'E3|Архитектура: схемы AS IS и TO BE|blocking'
    'E4|API сбора пользовательских действий|'
    'E5|Исследование хранилищ: ClickHouse vs Vertica|'
    'E6|ETL из Kafka в ClickHouse|'
)

create_label() {
    local name=$1 color=$2 description=$3
    $DRY_RUN && { echo "label: $name"; return; }
    gh label create "$name" --color "$color" --description "$description" --force >/dev/null
}

create_issue() {
    local title=$1 body=$2
    shift 2
    local args=()
    for label in "$@"; do
        [[ -n "$label" ]] && args+=(--label "$label")
    done
    if $DRY_RUN; then
        echo "issue: $title [${*}]"
        return
    fi
    gh issue create --title "$title" --body "$body" "${args[@]}"
}

for label in "${LABELS[@]}"; do
    IFS=':' read -r name color description <<<"$label"
    create_label "$name" "$color" "$description"
done

for epic in "${EPICS[@]}"; do
    IFS='|' read -r code title extra <<<"$epic"
    create_issue "[$code] $title" \
        "Эпик по пункту задания модуля. Состав и оценки — docs/planning.md." \
        'epic' "$extra"
done

number=0
for issue in "${ISSUES[@]}"; do
    IFS='|' read -r epic title points role depends <<<"$issue"
    number=$((number + 1))
    create_issue "$title" \
        "Эпик: $epic. Оценка: $points SP. Зависит от задач: $depends.

Подробности и определение Done — docs/planning.md." \
        "sp-$points" "$role"
done

echo "Готово: заведено ${#EPICS[@]} эпиков и $number задач."
