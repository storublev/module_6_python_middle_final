"""Ошибки бизнес-логики.

У каждой машиночитаемый код: клиент решает по нему, что делать дальше. Формат
ответа тот же, что у остальных сервисов кинотеатра — `{"code", "detail"}`.
HTTP-статусы назначает слой API, сервисы о HTTP не знают.
"""


class ServiceError(Exception):
    code: str = 'error'
    message: str = 'error'

    def __init__(self, message: str | None = None):
        super().__init__(message or self.message)
        self.message = message or self.message


class NotAuthenticatedError(ServiceError):
    code = 'not_authenticated'
    message = 'Authorization header with a bearer access token is required'


class TokenExpiredError(ServiceError):
    code = 'token_expired'
    message = 'Token has expired'


class TokenInvalidError(ServiceError):
    code = 'token_invalid'
    message = 'Token is malformed or signed with a wrong key'


class ForbiddenError(ServiceError):
    code = 'forbidden'
    message = 'Operation is not allowed'


class ServiceTokenInvalidError(ServiceError):
    # Служебный вход для админ-панели и других сервисов кинотеатра: своего
    # пользователя у них нет, поэтому опознаются общим секретом.
    code = 'service_token_invalid'
    message = 'Valid X-Service-Token header is required'


class NotFoundError(ServiceError):
    code = 'not_found'
    message = 'Object not found'


class TemplateNotFoundError(NotFoundError):
    code = 'template_not_found'
    message = 'Template not found'


class CampaignNotFoundError(NotFoundError):
    code = 'campaign_not_found'
    message = 'Campaign not found'


class LinkNotFoundError(NotFoundError):
    code = 'link_not_found'
    message = 'Short link not found or expired'


class ConfirmationLinkInvalidError(NotFoundError):
    # Одним кодом на все три случая — нет, истёк, уже использован: различать
    # их вслух значит подсказывать, какие токены существуют.
    code = 'confirmation_link_invalid'
    message = 'Confirmation link is invalid, expired or already used'


class ConflictError(ServiceError):
    code = 'conflict'
    message = 'Object already exists'


class TemplateCodeTakenError(ConflictError):
    code = 'template_code_taken'
    message = 'Template with this code already exists'


class BadRequestError(ServiceError):
    code = 'bad_request'
    message = 'Request is malformed'


class TemplateInvalidError(BadRequestError):
    # Шаблон менеджера проверяется при сохранении: без проверки он уронит
    # сборку письма у воркера или уйдёт в бесконечный цикл, и узнаем мы об
    # этом уже во время рассылки.
    code = 'template_invalid'
    message = 'Template is not valid'


class UnknownTimezoneError(BadRequestError):
    code = 'unknown_timezone'
    message = 'Unknown IANA timezone name'


class CampaignNotRunnableError(BadRequestError):
    code = 'campaign_not_runnable'
    message = 'Campaign is cancelled or already finished'
