"""Метка доступа фильма для сервиса авторизации.

Фильмы, вышедшие менее SUBSCRIPTION_PERIOD_YEARS лет назад (по умолчанию
трёх), доступны только по подписке: ETL записывает в документ фильма
access_level=subscription, а сервис контента пускает к ним только
пользователей с правом films.subscription (роль subscribers в сервисе
авторизации). Остальные фильмы — access_level=public, их видят все,
в том числе анонимные пользователи.

Сервису контента не нужно знать правило «три года»: он фильтрует по метке.
Правило меняется здесь, в одном месте.
"""

from datetime import date
from typing import Optional

from core.config import settings

PUBLIC = "public"
SUBSCRIPTION = "subscription"


def subscription_threshold(today: date, years: Optional[int] = None) -> date:
    """Дата, после которой фильм считается новинкой: сегодня минус years лет.

    Для 29 февраля в невисокосный год берётся 28 февраля.
    """
    years = settings.subscription_period_years if years is None else years
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


def access_level(creation_date: Optional[date], today: Optional[date] = None) -> str:
    """Метка доступа фильма на дату today.

    Фильм без даты выхода считается давним и доступен всем: иначе каталог
    без заполненных дат целиком ушёл бы под подписку.
    """
    if creation_date is None:
        return PUBLIC
    threshold = subscription_threshold(today or date.today())
    return SUBSCRIPTION if creation_date > threshold else PUBLIC
