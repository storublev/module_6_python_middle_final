"""Окно суток, в которое зрителю писать нельзя.

Считается в его собственном часовом поясе: у кинотеатра зрители от
Калининграда до Владивостока, и одно московское время для всех означает
письмо в три ночи половине страны. Ночная отправка — самая обидная ошибка
рассылки, о ней прямо предупреждает урок «Как испортить жизнь клиенту».

Окно проверяется дважды: сборщиком (собирать письмо, которое всё равно
подождёт утра, незачем) и отправителем прямо перед отправкой — письмо могло
пролежать в очереди, пока у зрителя наступила ночь.
"""

import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)


class QuietHours:
    """Тихие часы в часовом поясе зрителя."""

    def __init__(self, start_hour: int, end_hour: int, default_timezone: str) -> None:
        self._start = time(hour=start_hour)
        self._end = time(hour=end_hour)
        self._default = default_timezone

    def zone_of(self, name: str | None) -> ZoneInfo:
        zone_name = name or self._default
        try:
            return ZoneInfo(zone_name)
        except (ZoneInfoNotFoundError, ValueError):
            # Часовой пояс проверяется при сохранении профиля, но данные
            # приходят из чужого сервиса: испорченное значение не должно
            # ронять рассылку.
            logger.warning('Неизвестный часовой пояс %s, взят %s', zone_name, self._default)
            return ZoneInfo(self._default)

    def is_quiet(self, moment: datetime, zone_name: str | None) -> bool:
        """Тихое ли сейчас время у зрителя с этим часовым поясом."""
        local = moment.astimezone(self.zone_of(zone_name)).time()
        if self._start == self._end:
            # Окно нулевой длины: тихого времени нет вовсе.
            return False
        if self._start < self._end:
            return self._start <= local < self._end
        # Окно через полночь (21:00–09:00) — самый обычный случай.
        return local >= self._start or local < self._end

    def next_open(self, moment: datetime, zone_name: str | None) -> datetime:
        """Ближайший момент, когда писать снова можно, в UTC."""
        local = moment.astimezone(self.zone_of(zone_name))
        opening = local.replace(hour=self._end.hour, minute=0, second=0, microsecond=0)
        if opening <= local:
            opening += timedelta(days=1)
        return opening.astimezone(timezone.utc)
