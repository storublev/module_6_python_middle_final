"""Ошибки бизнес-логики.

У каждой машиночитаемый код: клиент решает по нему, что делать дальше, а
интерфейс показывает человеку понятный текст. Формат ответа тот же, что у
остальных сервисов кинотеатра — `{"code", "detail"}`. HTTP-статусы назначает
слой API, сервисы о HTTP не знают.
"""


class ServiceError(Exception):
    code: str = 'error'
    message: str = 'error'

    def __init__(self, message: str | None = None):
        super().__init__(message or self.message)
        self.message = message or self.message


# 401 — кто прислал запрос, неизвестно.

class NotAuthenticatedError(ServiceError):
    code = 'not_authenticated'
    message = 'Authorization header with a bearer access token is required'


class TokenExpiredError(ServiceError):
    code = 'token_expired'
    message = 'Token has expired'


class TokenInvalidError(ServiceError):
    code = 'token_invalid'
    message = 'Token is malformed or signed with a wrong key'


class TokenRevokedError(ServiceError):
    # Код тот же, что у сервиса авторизации: клиенту кинотеатра не нужно
    # различать, кто из сервисов заметил закрытую сессию.
    code = 'token_revoked'
    message = 'Session has been terminated, log in again'


# 403 — известно кто, но ему нельзя.

class ForbiddenError(ServiceError):
    code = 'forbidden'
    message = 'Operation is not allowed'


class NotScreeningHostError(ForbiddenError):
    code = 'not_screening_host'
    message = 'Only the host can manage this screening'


class NotBookingOwnerError(ForbiddenError):
    code = 'not_booking_owner'
    message = 'Only the guest who made the booking can change it'


class OwnScreeningError(ForbiddenError):
    code = 'own_screening'
    message = 'Host cannot book seats at their own screening'


class NotParticipantError(ForbiddenError):
    code = 'not_participant'
    message = 'Only the host and guests of the screening can rate each other'


# 404.

class NotFoundError(ServiceError):
    code = 'not_found'
    message = 'Object not found'


class ScreeningNotFoundError(NotFoundError):
    code = 'screening_not_found'
    message = 'Screening not found'


class BookingNotFoundError(NotFoundError):
    code = 'booking_not_found'
    message = 'Booking not found'


class FilmNotFoundError(NotFoundError):
    code = 'film_not_found'
    message = 'Film not found in the catalog'


# 409 — запрос верный, но состояние не позволяет.

class ConflictError(ServiceError):
    code = 'conflict'
    message = 'Conflict with the current state'


class NotEnoughSeatsError(ConflictError):
    code = 'not_enough_seats'
    message = 'Not enough free seats left'


class ScreeningClosedError(ConflictError):
    code = 'screening_closed'
    message = 'Screening has already started or was cancelled'


class AlreadyBookedError(ConflictError):
    code = 'already_booked'
    message = 'You already have a booking for this screening; change it instead'


class BookingCancelledError(ConflictError):
    code = 'booking_cancelled'
    message = 'Booking is already cancelled'


class CapacityBelowBookedError(ConflictError):
    code = 'capacity_below_booked'
    message = 'Capacity cannot be less than the number of booked seats'


class RatingTooEarlyError(ConflictError):
    code = 'rating_too_early'
    message = 'Participants can rate each other only after the screening starts'


class ScreeningCancelledError(ConflictError):
    code = 'screening_cancelled'
    message = 'Cancelled screening cannot be rated'


class AlreadyRatedError(ConflictError):
    code = 'already_rated'
    message = 'You have already rated this participant for this screening'


# 400 — клиент прислал то, чего не бывает.

class BadRequestError(ServiceError):
    code = 'bad_request'
    message = 'Request is malformed'


class FilmNotBookableError(BadRequestError):
    # ФТ-4: в кино всем составом идут на полнометражный фильм, а не на сериал.
    code = 'film_not_bookable'
    message = 'Only feature films (type "movie") can be booked'


class StartsTooSoonError(BadRequestError):
    code = 'starts_too_soon'
    message = 'Screening must start at least 30 minutes from now'


class StartsTooLateError(BadRequestError):
    code = 'starts_too_late'
    message = 'Screening cannot be scheduled more than a year ahead'


class CapacityOutOfRangeError(BadRequestError):
    code = 'capacity_out_of_range'
    message = 'Capacity is out of the allowed range'


class SeatsOutOfRangeError(BadRequestError):
    code = 'seats_out_of_range'
    message = 'Number of seats is out of the allowed range'


class NothingToChangeError(BadRequestError):
    code = 'nothing_to_change'
    message = 'Request changes nothing'


class InvalidRatingTargetError(BadRequestError):
    code = 'invalid_rating_target'
    message = 'Guests rate the host, the host rates guests'
