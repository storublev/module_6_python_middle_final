"""Служебный справочник контактов для сервиса уведомлений.

Воркеру уведомлений приходит только `user_id`, а письмо собрать нужно с
адресом и именем. Ходить сюда пользовательским access-токеном он не может —
своего пользователя у сервиса нет, — поэтому опознаётся общим секретом в
заголовке `X-Service-Token`.

Два эндпоинта закрывают два сценария рассылки: пачка по списку
идентификаторов (мгновенные уведомления и сегменты) и постраничный обход всех
пользователей (одинаковое письмо всем). Оба отдают контакты **пачкой**: на
тысячу адресатов — один запрос, а не тысяча, иначе рассылка положит сервис
авторизации, о чём прямо предупреждает задача урока про RabbitMQ.

Пока `AUTH_SERVICE_TOKEN` не задан, попасть сюда нельзя никому: пустое
ожидаемое значение не совпадает ни с одним присланным. Роутер при этом
подключён всегда, иначе спецификация OpenAPI зависела бы от настроек стенда.
"""

import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query

from api.dependencies import DirectoryServiceDep
from api.errors import error_responses
from api.v1.schemas import (
    MAX_CONTACTS_PER_REQUEST,
    ContactSchema,
    ContactsPageSchema,
    ContactsRequestSchema,
)
from core.config import settings
from services.errors import ServiceTokenInvalidError

router = APIRouter()

# Линтер принимает имя заголовка за пароль из-за слова token.
SERVICE_TOKEN_HEADER = 'X-Service-Token'  # noqa: S105
DEFAULT_PAGE_SIZE = 500


def check_service_token(
    token: Annotated[
        str | None,
        Header(alias=SERVICE_TOKEN_HEADER, description='Общий секрет служебного доступа'),
    ] = None,
) -> None:
    """Пускает только по служебному секрету.

    Сравнение постоянного времени: обычное `==` завершается на первом
    различии, и по времени ответа секрет подбирается посимвольно.
    """
    expected = settings.service_token.get_secret_value()
    # Незаданный секрет закрывает справочник для всех: сравнивать с пустой
    # строкой значило бы пускать по пустому заголовку.
    # Байты, а не строки: `compare_digest` на не-ASCII строке поднимает
    # TypeError, и заголовок с кириллицей давал бы 500 вместо 401.
    if not expected or not token or not secrets.compare_digest(token.encode('utf-8'), expected.encode('utf-8')):
        raise ServiceTokenInvalidError


@router.post(
    '/users/contacts',
    response_model=list[ContactSchema],
    summary='Контакты пользователей пачкой',
    description=(
        'Отдаёт почту, имя и часовой пояс перечисленных пользователей одним запросом. '
        'Ненайденные идентификаторы в ответе отсутствуют, порядок ответа не совпадает с порядком запроса — '
        'сопоставляйте по `id`. '
        f'За один раз можно спросить не больше {MAX_CONTACTS_PER_REQUEST} пользователей.'
    ),
    responses=error_responses(ServiceTokenInvalidError),
)
async def contacts(
    body: ContactsRequestSchema,
    directory: DirectoryServiceDep,
    _: Annotated[None, Depends(check_service_token)] = None,
) -> list[ContactSchema]:
    found = await directory.contacts(body.user_ids)
    return [ContactSchema.model_validate(contact) for contact in found]


@router.get(
    '/users/contacts',
    response_model=ContactsPageSchema,
    summary='Обход всех пользователей с почтой',
    description=(
        'Страница контактов по возрастанию идентификатора — для рассылки одинакового письма всем. '
        'Листание по ключу, а не по смещению: на миллионах записей `OFFSET` перечитывал бы всё предыдущее. '
        'Пользователи без почты пропускаются.'
    ),
    responses=error_responses(ServiceTokenInvalidError),
)
async def contacts_page(
    directory: DirectoryServiceDep,
    after: Annotated[
        UUID | None,
        Query(description='Идентификатор последнего пользователя прошлой страницы; в первом запросе не указывается'),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_CONTACTS_PER_REQUEST, description='Размер страницы')] = (
        DEFAULT_PAGE_SIZE
    ),
    _: Annotated[None, Depends(check_service_token)] = None,
) -> ContactsPageSchema:
    found = await directory.page(after, limit)
    # Следующий ключ отдаём, только если страница заполнена целиком: неполная
    # страница означает, что пользователи кончились, и лишний пустой запрос
    # рассылке делать незачем.
    next_after = found[-1].id if len(found) == limit else None
    return ContactsPageSchema.model_validate(
        {'items': [ContactSchema.model_validate(contact) for contact in found], 'next_after': next_after},
    )
