"""Мониторинг ошибок: неотловленные исключения уходят в Sentry.

Логи и Sentry отвечают на разные вопросы. Логи говорят, что происходило, — их
читают, когда уже знаешь, где искать. Sentry ловит то, о чём никто не знает:
каждое неотловленное исключение приходит со стектрейсом, контекстом запроса,
номером релиза и группировкой одинаковых ошибок.

Sentry необязателен: без DSN сервис работает как раньше и ничего никуда не
шлёт. Так его можно запустить локально, не поднимая контейнеры Sentry.

Чувствительные данные не отправляются: `send_default_pii=False` оставляет за
бортом заголовки с токеном и куки, а тела запросов не берутся вовсе.
"""

import logging

logger = logging.getLogger(__name__)

# Какая доля запросов попадает в Sentry как трассировка производительности.
# Ноль: за временем ответа следит Jaeger, а Sentry здесь нужен ради ошибок,
# и платить за двойную трассировку незачем.
TRACES_SAMPLE_RATE = 0.0


def configure_sentry(dsn: str, service: str, environment: str) -> None:
    """Подключает Sentry, если задан DSN."""
    if not dsn:
        logger.info('Sentry выключен: DSN не задан')
        return

    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=service,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=TRACES_SAMPLE_RATE,
        # Тела запросов не собираем: для отладки достаточно пути, параметров
        # и стектрейса.
        max_request_body_size='never',
        send_default_pii=False,
    )
    logger.info('Sentry включён, окружение %s', environment)
