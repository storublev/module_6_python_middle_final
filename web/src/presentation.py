"""Как показывать данные API человеку: время, склонения, звёзды, тексты ошибок.

Всё, что шаблоны делают с данными сверх «вывести как есть», собрано здесь и
подключается к Jinja2 фильтрами: шаблоны остаются разметкой, а правила
показа проверяются unit-тестами.
"""

import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

from core.config import settings

ZONE = ZoneInfo(settings.timezone)
WEEKDAYS = ('пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс')
MONTHS = ('янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек')

# Тексты для машиночитаемых кодов ошибок сервисов. Код без текста
# показывается общей фразой: зритель не должен видеть «not_enough_seats».
ERRORS = {
    'not_enough_seats': 'Столько свободных мест уже нет — кто-то успел раньше.',
    'screening_closed': 'Показ уже начался или отменён.',
    'already_booked': 'У вас уже есть бронь на этот показ — измените число мест в ней.',
    'own_screening': 'Это ваш показ: хост не бронирует места у себя.',
    'seats_out_of_range': 'За раз можно забронировать от 1 до 10 мест.',
    'film_not_bookable': 'Билеты продаются только на полнометражные фильмы.',
    'film_not_found': 'Такого фильма нет в каталоге.',
    'starts_too_soon': 'Показ можно назначить не раньше чем через 30 минут.',
    'starts_too_late': 'Показ можно назначить не дальше чем на год вперёд.',
    'capacity_out_of_range': 'Мест должно быть от 1 до 50.',
    'capacity_below_booked': 'Мест не может быть меньше, чем уже забронировано.',
    'nothing_to_change': 'Ничего не изменилось.',
    'booking_cancelled': 'Бронь уже отменена.',
    'rating_too_early': 'Оценить можно после начала показа.',
    'already_rated': 'Вы уже поставили эту оценку.',
    'not_participant': 'Оценивать могут только хост и гости показа.',
    'invalid_rating_target': 'Гость оценивает хоста, а хост — своих гостей.',
    'screening_cancelled': 'Показ отменён — оценивать нечего.',
    'not_screening_host': 'Управлять показом может только его хост.',
    'not_booking_owner': 'Это не ваша бронь.',
    'invalid_credentials': 'Неверный логин или пароль.',
    'login_taken': 'Такой логин уже занят.',
    'too_many_requests': 'Слишком много попыток. Подождите немного и попробуйте снова.',
    'validation_error': 'Проверьте заполнение формы.',
    'unavailable': 'Сервис временно недоступен. Попробуйте через минуту.',
    'login_required': 'Войдите, чтобы продолжить.',
    'token_revoked': 'Сеанс завершён: вы вышли или сменили пароль на другом устройстве. Войдите снова.',
}
GENERIC_ERROR = 'Не получилось. Попробуйте ещё раз.'

NOTICES = {
    'booked': 'Готово! Места забронированы — подтверждение придёт на почту.',
    'seats_changed': 'Число мест в брони изменено.',
    'booking_cancelled': 'Бронь отменена, места вернулись хосту.',
    'screening_created': 'Показ создан. Теперь его видят зрители в карточке фильма.',
    'screening_updated': 'Показ изменён. Гости получат письмо, если поменялись время или место.',
    'screening_cancelled': 'Показ отменён, гостям ушли письма.',
    'rated': 'Спасибо за оценку!',
    'welcome': 'Добро пожаловать в Practix!',
}


def error_text(code: str | None) -> str | None:
    if not code:
        return None
    return ERRORS.get(code, GENERIC_ERROR)


def notice_text(code: str | None) -> str | None:
    return NOTICES.get(code) if code else None


def local(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    return moment.astimezone(ZONE)


def when(value: str | datetime | None) -> str:
    """«сб, 17 окт, 19:00» — как время показа читают люди."""
    moment = local(value)
    if moment is None:
        return ''
    return f'{WEEKDAYS[moment.weekday()]}, {moment.day} {MONTHS[moment.month - 1]}, {moment:%H:%M}'


def input_value(value: str | datetime | None) -> str:
    """Значение для <input type="datetime-local">: местное время без пояса."""
    moment = local(value)
    return f'{moment:%Y-%m-%dT%H:%M}' if moment else ''


def plural(number: int, one: str, few: str, many: str) -> str:
    """Склонение по числу: 1 место, 2 места, 5 мест."""
    tail = number % 100
    if 11 <= tail <= 14:
        word = many
    elif tail % 10 == 1:
        word = one
    elif 2 <= tail % 10 <= 4:
        word = few
    else:
        word = many
    return f'{number} {word}'


def seats(number: int) -> str:
    return plural(number, 'место', 'места', 'мест')


def shows(number: int) -> str:
    return plural(number, 'показ', 'показа', 'показов')


def rating(summary: dict | None) -> str:
    """«★ 4,7 · 12 оценок» или «нет оценок»."""
    if not summary or not summary.get('votes'):
        return 'нет оценок'
    average = f'{summary["average"]:.1f}'.replace('.', ',')
    return f'★ {average} · {plural(summary["votes"], "оценка", "оценки", "оценок")}'


def hue(text: str) -> int:
    """Оттенок заглушки обложки: у каждого фильма свой, но всегда один и тот же."""
    return int(hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()[:4], 16) % 360


FILTERS = {'when': when, 'seats': seats, 'shows': shows, 'rating': rating, 'hue': hue, 'input_value': input_value}
