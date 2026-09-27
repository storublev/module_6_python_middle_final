#!/usr/bin/env bash
# Заводит доску задач в GitHub по описанию, переданному снаружи.
#
# Зачем скриптом: задач под тридцать, у каждой оценка, роль и связи; руками они
# заводятся полчаса, а при переносе в другой репозиторий — ещё раз.
#
# Зачем снаружи: задачи меняются каждый спринт, а способ их заводить — нет.
# Раньше список жил внутри скрипта, и под новый спринт скрипт копировался
# целиком. Теперь он один, а доски лежат рядом файлами: scripts/issues/*.txt.
#
# Требуется GitHub CLI (`gh auth login`). Запуск из корня репозитория:
#
#   ./scripts/create_issues.sh -f scripts/issues/sprint9.txt
#   ./scripts/create_issues.sh -f scripts/issues/sprint9.txt --dry-run
#   ./scripts/create_issues.sh -f scripts/issues/sprint9.txt --repo storublev/ugc_sprint_2
#   ./scripts/create_issues.sh -i 'E1|Проверить гипотезу|2|role-data|—'
#   ISSUES='E1|Первая|1|role-qa|—; E1|Вторая|2|role-qa|1' ./scripts/create_issues.sh
#   собрать-доску | ./scripts/create_issues.sh -f -
#
# Отдельный --repo нужен потому, что доску спринта наставник смотрит в
# репозитории спринта (ugc_sprint_1), а код живёт в репозитории модуля.
#
# ФОРМАТ. Поля разделяются «|», записи — «;», «,» или переводом строки:
#
#   E1|Заголовок задачи|2|role-qa|зависит от задач     задача (тип по умолчанию)
#   task |E1|Заголовок|2|role-qa|зависимости           она же явно
#   epic |E1|Название эпика|blocking                   эпик, метки через запятую
#   label|role-sre|fbca04|Роль: SRE                    своя метка с цветом
#
# Метки оценок и ролей заводятся сами по тому, что встретилось в задачах:
# отдельный список ролей рассинхронизировался бы с доской при первой же правке.
#
# ЗАПЯТАЯ ИЛИ ТОЧКА С ЗАПЯТОЙ. Перевод строки и «;» делят записи всегда. А «,»
# — только если весь ввод пришёл одной строкой без «;»: в заголовках запятые
# обычны («Каркас сервиса: настройки, журнал, слои»), и в файле доски, где на
# запись отводится строка, делить по ним нельзя. То есть «,» — удобство для
# короткого перечисления в -i и ISSUES, а «;» годится везде.
#
# Пустые строки и строки, начинающиеся с «#», пропускаются: в файле доски
# удобно держать комментарии и разделять задачи по эпикам.
set -euo pipefail

DRY_RUN=false
ISSUES_INPUT=${ISSUES:-}
PLANNING_URL=${PLANNING_URL:-'https://github.com/storublev/module_4_python_middle_dev/blob/main/docs/planning.md'}

usage() {
    sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case $1 in
        -f|--issues-file)
            [[ $# -ge 2 ]] || { echo "Не задан файл после $1" >&2; exit 1; }
            # «-» означает стандартный ввод: доску можно собрать другой
            # командой и передать сюда по конвейеру.
            if [[ $2 == '-' ]]; then
                ISSUES_INPUT=$(cat)
            else
                [[ -r $2 ]] || { echo "Файл не читается: $2" >&2; exit 1; }
                ISSUES_INPUT=$(cat "$2")
            fi
            shift 2 ;;
        -i|--issues)
            [[ $# -ge 2 ]] || { echo "Не задано описание после $1" >&2; exit 1; }
            ISSUES_INPUT=$2; shift 2 ;;
        --planning-url)
            [[ $# -ge 2 ]] || { echo "Не задан адрес после $1" >&2; exit 1; }
            PLANNING_URL=$2; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        --repo)
            [[ $# -ge 2 ]] || { echo "Не задан репозиторий после $1" >&2; exit 1; }
            export GH_REPO=$2; shift 2 ;;
        -h|--help) usage 0 ;;
        *) echo "Неизвестный аргумент: $1" >&2; usage 1 ;;
    esac
done

if [[ -z ${ISSUES_INPUT//[[:space:]]/} ]]; then
    echo 'Нечего заводить: передайте задачи через -f файлом, -i строкой или переменной ISSUES.' >&2
    echo 'Готовые доски — scripts/issues/*.txt, подробности — ./scripts/create_issues.sh --help' >&2
    exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
    echo 'Нужен GitHub CLI: brew install gh && gh auth login' >&2
    exit 1
fi

# Цвета меток по умолчанию: оценки — светлым, роли — синим, эпики и блокировки
# заметнее остальных.
COLOR_EPIC=6f42c1
COLOR_BLOCKING=b60205
COLOR_POINTS=fef2c0
COLOR_ROLE=1d76db

# Каждая запись приводится к виду «тип|поля», чтобы дальше не разбирать два
# написания одного и того же. Записи копятся строкой: см. про bash 3.2 ниже.
RECORDS=''
while IFS= read -r record; do
    record="${record#"${record%%[![:space:]]*}"}"   # пробелы слева
    record="${record%"${record##*[![:space:]]}"}"   # и справа
    [[ -z $record || $record == '#'* ]] && continue
    case ${record%%|*} in
        epic|task|label) RECORDS+="$record"$'\n' ;;
        *) RECORDS+="task|$record"$'\n' ;;          # тип опущен — это задача
    esac
done < <(
    # Запятая делит записи только в однострочном вводе без «;»: иначе она
    # разрезала бы заголовки вроде «Завести задачи в GitHub, расставить оценки
    # и метки», и все поля после неё съехали бы — оценка ушла бы в метку роли.
    if [[ $ISSUES_INPUT == *$'\n'* || $ISSUES_INPUT == *';'* ]]; then
        tr ';' '\n' <<<"$ISSUES_INPUT"
    else
        tr ',' '\n' <<<"$ISSUES_INPUT"
    fi
)

create_label() {
    local name=$1 color=$2 description=$3
    [[ -n $name ]] || return 0
    $DRY_RUN && { echo "метка: $name"; return; }
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
        echo "задача: $title [${*}]"
        return
    fi
    gh issue create --title "$title" --body "$body" "${args[@]}"
}

# Метки заводятся до задач: у несуществующей метки `gh issue create` падает.
# Копятся они в строке (по метке на строку), а не в массиве: в macOS до сих пор
# поставляется bash 3.2 без ассоциативных массивов, а обычный массив под
# `set -u` требует оговорок на каждом обращении. Строка проще и работает везде.
LABELS=''

remember_label() {
    local name=$1 color=$2 description=$3
    [[ -n $name ]] || return 0
    # Первое описание метки выигрывает: явная запись label стоит в файле выше
    # задач, и значение по умолчанию не должно её затирать. Сверка глобом, а не
    # grep: в имени метки могут оказаться символы, значимые для регулярного
    # выражения.
    case $'\n'$LABELS in
        *$'\n'"$name|"*) return 0 ;;
    esac
    LABELS+="$name|$color|$description"$'\n'
}

while IFS='|' read -r kind one two three four _; do
    [[ -n $kind ]] || continue
    case $kind in
        label) remember_label "$one" "${two:-$COLOR_ROLE}" "${three:-}" ;;
        epic)
            remember_label 'epic' "$COLOR_EPIC" 'Эпик — пункт задания спринта'
            for extra in ${three//,/ }; do
                remember_label "$extra" "$COLOR_BLOCKING" \
                    'Блокирующая задача: до её ревью реализация не начинается'
            done ;;
        task)
            [[ -n $three ]] && remember_label "sp-$three" "$COLOR_POINTS" "Оценка $three story points"
            [[ -n $four ]] && remember_label "$four" "$COLOR_ROLE" 'Роль исполнителя' ;;
    esac
done <<<"$RECORDS"

while IFS='|' read -r name color description; do
    [[ -n $name ]] || continue
    create_label "$name" "$color" "$description"
done <<<"$LABELS"

epics=0 tasks=0
while IFS='|' read -r kind one two three four five; do
    [[ -n $kind ]] || continue
    case $kind in
        label) continue ;;
        epic)
            create_issue "[$one] $two" \
                "Эпик по пункту задания спринта. Состав, оценки и зависимости — $PLANNING_URL" \
                'epic' "${three//,/ }"
            epics=$((epics + 1)) ;;
        task)
            create_issue "$two" \
                "Эпик: $one. Оценка: $three SP. Зависит от задач: ${five:-—}.

Подробности и определение Done — $PLANNING_URL" \
                "sp-$three" "$four"
            tasks=$((tasks + 1)) ;;
    esac
done <<<"$RECORDS"

echo "Готово: эпиков — $epics, задач — $tasks."
