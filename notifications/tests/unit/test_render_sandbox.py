"""Процесс сборки писем: шаблон не может остановить API и воркер.

Здесь поднимается настоящий отдельный процесс — как в работе. Это медленнее
остальных unit-тестов на доли секунды, зато проверяется именно то, ради чего
процесс заведён: его можно остановить.
"""

import asyncio
import sys
import time

import pytest

from services.errors import TemplateInvalidError
from services.render_sandbox import IsolatedEngine
from tests.unit.fakes import template

# Три вложенных цикла по тысяче шагов без вывода: размер письма не растёт,
# каждый range в пределах, а работы — на миллиард итераций.
ENDLESS = '{% for a in range(1000) %}{% for b in range(1000) %}{% for c in range(1000) %}' \
          '{% endfor %}{% endfor %}{% endfor %}'


@pytest.fixture
async def sandbox() -> IsolatedEngine:
    engine = IsolatedEngine(timeout=0.5, memory_limit_bytes=256 * 1024 * 1024)
    yield engine
    await engine.close()


async def test_preview_is_rendered_in_separate_process(sandbox: IsolatedEngine) -> None:
    """Обычный шаблон собирается в отдельном процессе так же, как на месте."""
    subject, body = await sandbox.preview('Привет, {{ first_name }}', '<p>{{ film_title }}</p>')

    assert subject == 'Привет, Томас'
    assert body == '<p>Матрица</p>'


async def test_endless_template_is_stopped_and_engine_keeps_working(sandbox: IsolatedEngine) -> None:
    """Шаблон, занявший процессор, останавливается по таймеру, а следующий собирается.

    Раньше такой шаблон собирался прямо в обработчике запроса и держал весь
    процесс API: остальные запросы ждали вместе с ним.
    """
    started = time.monotonic()
    with pytest.raises(TemplateInvalidError, match='too long'):
        await sandbox.validate('Тема', ENDLESS)
    assert time.monotonic() - started < 10

    subject, _ = await sandbox.preview('Снова работаю', '<p>ok</p>')
    assert subject == 'Снова работаю'


async def test_event_loop_is_not_blocked_while_rendering(sandbox: IsolatedEngine) -> None:
    """Пока тяжёлый шаблон собирается, цикл событий обслуживает другие задачи."""
    ticks = 0

    async def heartbeat() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    beating = asyncio.create_task(heartbeat())
    with pytest.raises(TemplateInvalidError):
        await sandbox.validate('Тема', ENDLESS)
    beating.cancel()

    assert ticks >= 5


async def test_one_broken_letter_does_not_break_the_batch(sandbox: IsolatedEngine) -> None:
    """Ошибка одного письма пачки оставляет остальные собранными."""
    letter = template(subject='Тема', body='{{ items|sum }}')

    results = await sandbox.render_many(letter, [{'items': [1, 2]}, {'items': ['a', 1]}, {'items': [3]}])

    assert results[0] == ('Тема', '3')
    assert isinstance(results[1], TemplateInvalidError)
    assert results[2] == ('Тема', '3')


async def test_dead_process_is_replaced(sandbox: IsolatedEngine) -> None:
    """Если процесс сборки умер (например, его убила система за память), поднимается новый."""
    await sandbox.preview('Тема', 'тело')
    sandbox._process.kill()  # noqa: SLF001 - имитируем внешнюю причину смерти процесса
    sandbox._process.join()  # noqa: SLF001

    subject, _ = await sandbox.preview('После перезапуска', 'тело')

    assert subject == 'После перезапуска'


@pytest.mark.skipif(sys.platform != 'linux', reason='предел RLIMIT_AS соблюдает только Linux')
async def test_memory_hungry_template_hits_the_limit(sandbox: IsolatedEngine) -> None:
    """Шаблон, просящий полтора гигабайта, упирается в предел памяти процесса сборки.

    Урезанный язык такой шаблон не ловит: каждая операция по отдельности
    разрешена. Ровно для таких случаев и нужен третий слой — процесс с
    пределом памяти.
    """
    with pytest.raises(TemplateInvalidError):
        await sandbox.validate('Тема', "{{ (['x' * 1400000] * 1000)|join }}")

    subject, _ = await sandbox.preview('Жив', 'тело')
    assert subject == 'Жив'
