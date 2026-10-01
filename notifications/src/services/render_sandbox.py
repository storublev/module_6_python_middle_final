"""Сборка писем в отдельном процессе с пределами памяти и времени.

Зачем процесс, а не поток. Jinja2 собирает письмо чистым Python-кодом, и
тяжёлый шаблон держит интерпретатор: в асинхронном обработчике это значит, что
весь API или весь воркер стоит, пока собирается одно письмо. Поток тут не
поможет — его нельзя остановить снаружи, а GIL он делит с циклом событий.
Процесс можно убить, и память у него своя: шаблон, съевший гигабайт, уронит
только его.

Как устроено:

* процесс один на API или на воркер, живёт долго и получает задания по
  `Pipe` — запуск интерпретатора стоит сотни миллисекунд, и платить их на
  каждое письмо незачем. Пачка писем уходит одним заданием;
* в процессе стоит предел адресного пространства (`RLIMIT_AS`): исчерпав его,
  шаблон получает `MemoryError`, а не раздувает машину;
* у каждого письма свой таймер (`setitimer`): письмо, собиравшееся дольше
  предела, получает ошибку, а остальные письма пачки собираются дальше;
* снаружи — страховка на случай, когда процесс завис в коде на C, где
  таймер не срабатывает: не ответил вовремя — убиваем и при следующем
  задании поднимаем новый;
* `spawn`, а не `fork`: в процессе с циклом событий и потоками `fork`
  копирует замки в неизвестном состоянии.
"""

import asyncio
import logging
import multiprocessing
from collections.abc import Callable, Sequence
from functools import partial
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Any

from models.notification import Template
from services.errors import TemplateInvalidError
from services.renderer import Renderer, TemplateEngine

logger = logging.getLogger(__name__)

# Сколько ждать, пока новый процесс поднимется и ответит. Интерпретатор с
# Jinja2 стартует за доли секунды, запас — на нагруженную машину.
STARTUP_TIMEOUT = 30.0
# Запас сверх суммы таймеров писем: передача пачки по Pipe тоже занимает время.
CALL_SLACK = 5.0

TIMEOUT_MESSAGE = 'Template rendering took too long'
RESOURCES_MESSAGE = 'Template rendering ran out of resources'


class RenderTimeout(BaseException):  # noqa: N818 - это сигнал, а не ошибка
    """Таймер письма сработал.

    Наследник `BaseException`, а не `Exception`: иначе его перехватил бы
    общий обработчик сборки и превратил в «шаблон упал», а внутри Jinja2 — в
    произвольное место кода.
    """


class IsolatedEngine(TemplateEngine):
    """Сборка писем в отдельном процессе."""

    def __init__(self, timeout: float, memory_limit_bytes: int) -> None:
        super().__init__()
        self._timeout = timeout
        self._memory_limit = memory_limit_bytes
        self._context = multiprocessing.get_context('spawn')
        self._process: BaseProcess | None = None
        self._conn: Connection | None = None
        # Процесс один, а запросов к нему может прийти несколько сразу —
        # задания идут по одному, иначе ответы перепутаются.
        self._lock = asyncio.Lock()

    async def validate(self, subject: str, body: str) -> None:
        await self._call(('validate', subject, body, []), items=1)

    async def preview(self, subject: str, body: str) -> tuple[str, str]:
        rendered = await self._call(('preview', subject, body, []), items=1)
        return rendered

    async def render_many(
        self, template: Template, letters: Sequence[dict[str, Any]],
    ) -> list[tuple[str, str] | TemplateInvalidError]:
        if not letters:
            return []
        outcome = await self._call(('render', template.subject, template.body, list(letters)), items=len(letters))
        return [
            tuple(payload) if status == 'ok' else TemplateInvalidError(payload)  # type: ignore[misc]
            for status, payload in outcome
        ]

    async def close(self) -> None:
        self._kill()

    async def _call(self, job: tuple[str, str, str, list[dict[str, Any]]], items: int) -> Any:
        async with self._lock:
            conn = await self._ensure_started()
            try:
                conn.send(job)
                # Ожидание ответа — в потоке: `poll` блокирующий, а цикл событий
                # должен обслуживать остальные запросы, пока письмо собирается.
                ready = await asyncio.to_thread(conn.poll, self._timeout * items + CALL_SLACK)
                if not ready:
                    logger.error('Процесс сборки писем не ответил вовремя и будет перезапущен')
                    self._kill()
                    raise TemplateInvalidError(TIMEOUT_MESSAGE)
                status, payload = conn.recv()
            except (EOFError, OSError, BrokenPipeError) as error:
                # Процесс умер посреди задания — скорее всего, его убила
                # система за память. Следующее задание поднимет новый.
                logger.error('Процесс сборки писем завершился: %s', error)
                self._kill()
                raise TemplateInvalidError(RESOURCES_MESSAGE) from error
            except asyncio.CancelledError:
                # Задание брошено, но процесс его ещё собирает и ответит потом —
                # чужому запросу. Безопаснее начать с чистого процесса.
                self._kill()
                raise
        if status == 'error':
            raise TemplateInvalidError(payload)
        return payload

    async def _ensure_started(self) -> Connection:
        if self._process is not None and self._process.is_alive() and self._conn is not None:
            return self._conn
        self._kill()
        parent, child = self._context.Pipe()
        process = self._context.Process(
            target=serve, args=(child, self._timeout, self._memory_limit), name='render-sandbox', daemon=True,
        )
        process.start()
        child.close()
        if not await asyncio.to_thread(parent.poll, STARTUP_TIMEOUT):
            process.kill()
            raise TemplateInvalidError('Template renderer did not start')
        parent.recv()
        self._process, self._conn = process, parent
        return parent

    def _kill(self) -> None:
        if self._process is not None and self._process.is_alive():
            self._process.kill()
            self._process.join(timeout=5)
        if self._conn is not None:
            self._conn.close()
        self._process = None
        self._conn = None


def serve(conn: Connection, timeout: float, memory_limit: int) -> None:
    """Цикл процесса сборки: получает задания и отвечает на них."""
    import signal

    _limit_memory(memory_limit)

    def on_timer(*_: Any) -> None:
        raise RenderTimeout

    signal.signal(signal.SIGALRM, on_timer)
    renderer = Renderer()
    conn.send('ready')
    while True:
        try:
            job = conn.recv()
        except EOFError:
            return
        conn.send(_run(renderer, job, timeout))


def _run(renderer: Renderer, job: tuple[str, str, str, list[dict[str, Any]]], timeout: float) -> tuple[str, Any]:
    """Выполняет одно задание. Таймер ставится на каждое письмо отдельно."""
    kind, subject, body, letters = job
    if kind == 'render':
        return 'ok', [
            _guarded(partial(renderer.render_source, subject, body, data), timeout) for data in letters
        ]
    if kind == 'validate':
        return _guarded(partial(renderer.validate, subject, body), timeout)
    return _guarded(partial(_preview, renderer, subject, body), timeout)


def _preview(renderer: Renderer, subject: str, body: str) -> tuple[str, str]:
    renderer.validate(subject, body)
    return renderer.render_probe(subject, body)


def _guarded(action: Callable[[], Any], timeout: float) -> tuple[str, Any]:
    """Выполняет сборку под таймером; ошибку возвращает текстом, а не поднимает."""
    import signal

    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        return 'ok', action()
    except RenderTimeout:
        return 'error', TIMEOUT_MESSAGE
    except TemplateInvalidError as error:
        return 'error', error.message
    except MemoryError:
        return 'error', RESOURCES_MESSAGE
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def _limit_memory(limit: int) -> None:
    """Ставит предел адресного пространства процесса.

    На Linux, где сервис работает, предел соблюдается. macOS `RLIMIT_AS` не
    соблюдает или не даёт поставить — там остаётся таймер и страховка
    снаружи, что для разработки достаточно.
    """
    if limit <= 0:
        return
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ImportError, ValueError, OSError) as error:
        logger.warning('Предел памяти процесса сборки не поставлен: %s', error)
