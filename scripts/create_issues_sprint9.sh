#!/usr/bin/env bash
# Заводит на доске GitHub задачи девятого спринта по docs/planning-sprint9.md.
#
# Устроен так же, как scripts/create_issues.sh восьмого спринта, и отличается
# только списком эпиков, задач и меток ролей: доску удобнее заводить одной
# командой, а не тридцатью кликами, и повторять её в репозитории спринта.
#
# Требуется GitHub CLI (`gh auth login`). Запуск из корня репозитория:
#   ./scripts/create_issues_sprint9.sh                 # завести задачи здесь
#   ./scripts/create_issues_sprint9.sh --dry-run       # только показать, что будет заведено
#   ./scripts/create_issues_sprint9.sh --repo o/name   # завести в другом репозитории
set -euo pipefail

DRY_RUN=false
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run) DRY_RUN=true; shift ;;
        --repo) export GH_REPO=$2; shift 2 ;;
        *) echo "Неизвестный аргумент: $1" >&2; exit 1 ;;
    esac
done

# Ссылка на декомпозицию: в репозитории спринта файла нет, а доска должна
# вести к нему сама.
PLANNING_URL='https://github.com/storublev/module_4_python_middle_dev/blob/main/docs/planning-sprint9.md'

if ! command -v gh >/dev/null 2>&1; then
    echo 'Нужен GitHub CLI: brew install gh && gh auth login' >&2
    exit 1
fi

# Метки оценок и ролей. Роли восьмого спринта (ETL, backend UGC) в этом спринте
# не нужны, зато появилась SRE: логи, мониторинг ошибок и CI.
LABELS=(
    'epic:6f42c1:Эпик — пункт задания спринта'
    'blocking:b60205:Блокирующая задача: до её ревью реализация не начинается'
    'sp-1:c2e0c6:Оценка 1 story point'
    'sp-2:c2e0c6:Оценка 2 story points'
    'sp-3:fef2c0:Оценка 3 story points'
    'sp-5:f9d0c4:Оценка 5 story points'
    'role-architect:0e8a16:Роль: архитектор'
    'role-content:1d76db:Роль: backend сервиса пользовательского контента'
    'role-data:5319e7:Роль: данные и хранилища'
    'role-qa:d93f0b:Роль: QA и DevOps'
    'role-sre:fbca04:Роль: SRE — логи, мониторинг, CI'
)

# Задача — строка «эпик|заголовок|оценка|роль|зависимости». Порядок тот же,
# что в docs/planning-sprint9.md.
ISSUES=(
    'E1|Выписать требования к сервису пользовательского контента|3|role-architect|—'
    'E1|Декомпозировать задание спринта на эпики и задачи, оценить|2|role-architect|1'
    'E1|Завести задачи на доске GitHub, расставить оценки и метки|2|role-qa|2'
    'E2|Спроектировать схему данных под оба хранилища|3|role-data|1'
    'E2|Поднять кластер MongoDB и PostgreSQL для исследования|3|role-data|4'
    'E2|Написать генератор данных: не менее 10 000 000 записей|3|role-data|5'
    'E2|Замерить чтение на загруженных данных по сценариям UGC|3|role-data|6'
    'E2|Замерить чтение данных, поступающих в реальном времени|3|role-data|7'
    'E2|Свести результаты в README, записать ADR о выборе хранилища|1|role-architect|8'
    'E3|ADR: отдельный сервис против доработки сервиса сбора событий|2|role-architect|9'
    'E3|Каркас сервиса на FastAPI: настройки, журнал, слои, MongoDB|3|role-content|10'
    'E3|Контракт и модели: лайк, рецензия, закладка|3|role-content|4'
    'E3|CRUD лайков и средняя оценка фильма|3|role-content|11, 12'
    'E3|CRUD закладок|2|role-content|11, 12'
    'E3|CRUD рецензий с лайками рецензий и сортировками списка|3|role-content|13'
    'E3|Маршрут в nginx, аутентификация по токену, OpenAPI и README|2|role-content|11'
    'E4|Unit-тесты бизнес-логики на хранилище в памяти|5|role-qa|13, 14, 15'
    'E4|Функциональные тесты в изолированном compose с MongoDB|5|role-qa|16, 17'
    'E5|Поднять ELK отдельным compose: Elasticsearch, Logstash, Kibana|3|role-sre|—'
    'E5|Настроить сбор логов сервисов и nginx|3|role-sre|19'
    'E5|Привести журналы сервисов к единому JSON-формату с X-Request-Id|3|role-sre|20'
    'E5|Собрать Data View в Kibana и проверить поиск по идентификатору запроса|2|role-sre|21'
    'E5|Поднять Sentry и подключить к сервисам|3|role-sre|—'
    'E5|Описать запуск наблюдаемости в README|2|role-sre|22, 23'
    'E6|Workflow GitHub Actions: запуск линтеров ruff и flake8|3|role-sre|—'
    'E6|Добавить проверку типов mypy и привести код в порядок|3|role-sre|25'
    'E6|Матрица версий Python 3.10, 3.11, 3.12 через strategy|3|role-sre|25'
    'E6|Уведомление об успешном прохождении CI в Telegram|2|role-sre|25'
)

# Блокирующий эпик один — исследование: пока хранилище не выбрано и выбор не
# подтверждён замерами, писать API не на чем.
declare -a EPICS=(
    'E1|Планирование и требования|'
    'E2|Исследование хранилища: MongoDB vs PostgreSQL|blocking'
    'E3|Сервис пользовательского контента|'
    'E4|Тесты сервиса|'
    'E5|Логи и мониторинг ошибок|'
    'E6|Непрерывная интеграция|'
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
        "Эпик по пункту задания девятого спринта. Состав, оценки и зависимости — $PLANNING_URL" \
        'epic' "$extra"
done

number=0
for issue in "${ISSUES[@]}"; do
    IFS='|' read -r epic title points role depends <<<"$issue"
    number=$((number + 1))
    create_issue "$title" \
        "Эпик: $epic. Оценка: $points SP. Зависит от задач: $depends.

Подробности и определение Done — $PLANNING_URL" \
        "sp-$points" "$role"
done

echo "Готово: заведено ${#EPICS[@]} эпиков и $number задач."
