"""Ошибки бизнес-логики.

У каждой ошибки есть машиночитаемый код: по нему клиент решает, что делать
дальше. Например, на token_expired стоит обновить пару токенов, а на
token_invalid и token_revoked — заново войти. HTTP-статусы назначает слой API
по категории ошибки, сервисы о HTTP не знают.
"""


class ServiceError(Exception):
    code: str = 'error'
    message: str = 'error'

    def __init__(self, message: str | None = None):
        super().__init__(message or self.message)
        self.message = message or self.message


class AuthenticationError(ServiceError):
    """Не удалось установить, кто делает запрос."""


class NotAuthenticatedError(AuthenticationError):
    code = 'not_authenticated'
    message = 'Authorization header with a bearer access token is required'


class InvalidCredentialsError(AuthenticationError):
    # Одинаковый ответ для неизвестного логина и неверного пароля: по нему
    # нельзя узнать, зарегистрирован ли логин.
    code = 'invalid_credentials'
    message = 'Invalid login or password'


class TokenExpiredError(AuthenticationError):
    code = 'token_expired'
    message = 'Token has expired'


class TokenInvalidError(AuthenticationError):
    code = 'token_invalid'
    message = 'Token is malformed or its signature is invalid'


class TokenRevokedError(AuthenticationError):
    code = 'token_revoked'
    message = 'Session has been terminated, log in again'


class ForbiddenError(ServiceError):
    """Кто делает запрос, известно, но выполнить его нельзя."""


class PermissionDeniedError(ForbiddenError):
    code = 'permission_denied'
    message = 'Not enough permissions'


class WrongPasswordError(ForbiddenError):
    code = 'wrong_password'
    message = 'Current password is incorrect'


class NotFoundError(ServiceError):
    """Запрошенного объекта нет."""


class UserNotFoundError(NotFoundError):
    code = 'user_not_found'
    message = 'User not found'


class RoleNotFoundError(NotFoundError):
    code = 'role_not_found'
    message = 'Role not found'


class RoleNotAssignedError(NotFoundError):
    code = 'role_not_assigned'
    message = 'User does not have this role'


class ProviderNotFoundError(NotFoundError):
    code = 'provider_not_found'
    message = 'Unknown social provider or it is not configured'


class SocialAccountNotLinkedError(NotFoundError):
    code = 'social_account_not_linked'
    message = 'No account of this social provider is linked'


class ConflictError(ServiceError):
    """Запрос противоречит текущему состоянию данных."""


class LoginTakenError(ConflictError):
    code = 'login_taken'
    message = 'Login is already taken'


class RoleNameTakenError(ConflictError):
    code = 'role_name_taken'
    message = 'Role with this name already exists'


class SocialAccountTakenError(ConflictError):
    code = 'social_account_taken'
    message = 'This social account is already linked to another user'


class LastLoginMethodError(ConflictError):
    # Открепив последний способ войти, пользователь потерял бы доступ к аккаунту.
    code = 'last_login_method'
    message = 'Set a password or link another social account before unlinking this one'


class PasswordAlreadySetError(ConflictError):
    code = 'password_already_set'
    message = 'Current password is required to change an existing password'


class OAuthStateInvalidError(AuthenticationError):
    # Возврат от поставщика не соответствует начатому входу: просрочен, уже
    # использован или пришёл не от нас.
    code = 'oauth_state_invalid'
    message = 'Login through the social provider has expired, start again'


class OAuthRejectedError(AuthenticationError):
    code = 'oauth_rejected'
    message = 'Social provider did not confirm the login, start again'


class SocialLinkExpiredError(AuthenticationError):
    # Вход, с которого начали привязку аккаунта, за это время закончился:
    # пользователь вышел или сменил пароль.
    code = 'social_link_expired'
    message = 'The session that started linking is no longer valid, log in and start again'


class ServiceUnavailableError(ServiceError):
    """Внешняя система временно недоступна: запрос стоит повторить позже."""

    code = 'service_unavailable'
    message = 'Service temporarily unavailable, retry later'


class OAuthProviderUnavailableError(ServiceUnavailableError):
    code = 'oauth_provider_unavailable'
    message = 'Social provider is unavailable, try again later'


class TooManyRequestsError(ServiceError):
    """Исчерпан лимит попыток входа или регистрации."""

    code = 'too_many_requests'
    message = 'Too many attempts, retry later'

    def __init__(self, retry_after: int, message: str | None = None):
        super().__init__(message)
        # Через сколько секунд попытку можно повторить.
        self.retry_after = retry_after
