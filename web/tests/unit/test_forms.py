"""Формы: вход, сессия в cookie, бронь, показ и оценка — PRG и понятные ошибки."""

from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

import httpx

from tests.unit.conftest import FILM_ID, HOST_ID, SCREENING, SCREENING_ID, USER_ID, Stand, token

BOOKING = {
    'id': '9a7c2f1e-1d5b-4a8e-b3a1-2f4b7c9d0e11', 'screening_id': SCREENING_ID, 'guest_id': USER_ID,
    'guest_name': 'Тринити', 'seats': 2, 'status': 'active', 'created_at': '2026-10-02T10:00:00Z',
    'updated_at': '2026-10-02T10:00:00Z',
}


def location(response: httpx.Response) -> tuple[str, dict[str, list[str]]]:
    parts = urlsplit(response.headers['location'])
    return parts.path, parse_qs(parts.query)


def test_login_puts_tokens_into_httponly_cookies(stand: Stand):
    """Вход кладёт токены в cookie с HttpOnly и SameSite=Lax и возвращает туда, откуда пришли."""
    stand.auth.routes['POST /auth/api/v1/login'] = {'access_token': token(), 'refresh_token': 'r1'}
    stand.auth.routes['GET /auth/api/v1/users/me'] = {'id': USER_ID, 'login': 'trinity', 'first_name': 'Тринити'}

    response = stand.client.post('/login', data={'login': 'trinity', 'password': 'pw', 'next_url': '/me/bookings'})

    assert (response.status_code, response.headers['location']) == (HTTPStatus.SEE_OTHER, '/me/bookings')
    cookies = response.headers.get_list('set-cookie')
    access = next(c for c in cookies if c.startswith('practix_access='))
    assert 'httponly' in access.lower() and 'samesite=lax' in access.lower()
    assert any(c.startswith('practix_name=%D0%A2') for c in cookies)  # «Тринити», закодированное для заголовка


def test_wrong_password_returns_to_form_with_message(stand: Stand):
    """Неверный пароль — обратно к форме с понятным текстом, без токенов."""
    stand.auth.routes['POST /auth/api/v1/login'] = (401, {'code': 'invalid_credentials', 'detail': 'x'})

    response = stand.client.post('/login', data={'login': 'neo', 'password': 'bad'})
    page = stand.client.get(response.headers['location'])

    assert location(response)[1]['error'] == ['invalid_credentials']
    assert 'Неверный логин или пароль' in page.text
    assert 'practix_access' not in response.headers.get('set-cookie', '')


def test_login_does_not_redirect_to_other_site(stand: Stand):
    """Адрес возврата — только путь своего сайта: «//evil.example» превращается в главную."""
    stand.auth.routes['POST /auth/api/v1/login'] = {'access_token': token(), 'refresh_token': 'r1'}
    stand.auth.routes['GET /auth/api/v1/users/me'] = {'id': USER_ID, 'login': 'neo'}

    response = stand.client.post('/login', data={'login': 'neo', 'password': 'pw', 'next_url': '//evil.example/x'})

    assert response.headers['location'] == '/'


def test_register_signs_up_logs_in_and_saves_name(stand: Stand):
    """Регистрация: учётная запись, вход и имя с почтой в профиль — имя увидят хосты."""
    stand.auth.routes['POST /auth/api/v1/signup'] = (201, {'id': USER_ID, 'login': 'neo'})
    stand.auth.routes['POST /auth/api/v1/login'] = {'access_token': token(), 'refresh_token': 'r1'}
    stand.auth.routes['PATCH /auth/api/v1/users/me/profile'] = {'id': USER_ID}
    stand.auth.routes['GET /auth/api/v1/users/me'] = {'id': USER_ID, 'login': 'neo', 'first_name': 'Томас'}

    response = stand.client.post(
        '/register', data={'login': 'neo', 'password': 'followtherabbit', 'first_name': 'Томас', 'email': 'n@e.ru'},
    )

    assert response.status_code == HTTPStatus.SEE_OTHER
    assert stand.auth.body('PATCH', '/auth/api/v1/users/me/profile') == {'first_name': 'Томас', 'email': 'n@e.ru'}


def test_expired_access_is_refreshed_transparently(stand: Stand):
    """Истёкший access-токен меняется по refresh-токену незаметно: страница открывается, cookie обновлены."""
    stand.client.cookies.set('practix_access', token(expires_in=-10))
    stand.client.cookies.set('practix_refresh', 'old-refresh')
    stand.auth.routes['POST /auth/api/v1/token/refresh'] = {'access_token': token(), 'refresh_token': 'new-refresh'}
    stand.booking.routes['GET /booking/api/v1/me/bookings'] = {'items': [], 'total': 0, 'page_number': 1,
                                                               'page_size': 50}

    response = stand.client.get('/me/bookings')

    assert response.status_code == HTTPStatus.OK
    assert stand.auth.body('POST', '/auth/api/v1/token/refresh') == {'refresh_token': 'old-refresh'}
    assert 'practix_refresh=new-refresh' in response.headers['set-cookie']


def test_rejected_refresh_closes_session(stand: Stand):
    """refresh-токен отозван — cookie удаляются, кабинет отправляет ко входу."""
    stand.client.cookies.set('practix_access', token(expires_in=-10))
    stand.client.cookies.set('practix_refresh', 'revoked')
    stand.auth.routes['POST /auth/api/v1/token/refresh'] = (401, {'code': 'token_revoked', 'detail': 'x'})

    response = stand.client.get('/me/bookings')

    path, query = location(response)
    assert (path, query['next'], query['error']) == ('/login', ['/me/bookings'], ['login_required'])
    assert 'practix_access=""' in response.headers['set-cookie']


def test_forged_token_is_not_a_session(stand: Stand):
    """Токен, подписанный чужим ключом, — не вход: зритель видит сайт гостем."""
    stand.client.cookies.set('practix_access', token().rsplit('.', 1)[0] + '.forged')

    assert stand.client.get('/me/screenings').headers['location'].startswith('/login')


def test_cross_site_post_is_rejected(stand: Stand):
    """POST с чужого сайта (Origin не наш) отклоняется до обработки — защита от CSRF."""
    stand.login()

    response = stand.client.post(
        f'/screenings/{SCREENING_ID}/book', data={'seats': 1}, headers={'Origin': 'https://evil.example'},
    )

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert not [r for r in stand.booking.requests if r.method == 'POST']


def test_booking_success_goes_to_my_bookings(stand: Stand):
    """Бронь: запрос с токеном зрителя и числом мест, затем «Мои брони» с сообщением."""
    stand.login()
    stand.booking.routes[f'POST /booking/api/v1/screenings/{SCREENING_ID}/bookings'] = (201, BOOKING)

    response = stand.client.post(f'/screenings/{SCREENING_ID}/book', data={'seats': 2},
                                 headers={'Origin': 'http://testserver'})

    request = stand.booking.last('POST', f'/booking/api/v1/screenings/{SCREENING_ID}/bookings')
    assert request.headers['authorization'].startswith('Bearer ')
    assert stand.booking.body('POST', f'/booking/api/v1/screenings/{SCREENING_ID}/bookings') == {'seats': 2}
    assert location(response) == ('/me/bookings', {'notice': ['booked']})


def test_overbooking_returns_to_film_with_reason(stand: Stand):
    """Мест не хватило — обратно в карточку фильма к тому же хосту, с объяснением по-русски."""
    stand.login()
    stand.booking.routes[f'POST /booking/api/v1/screenings/{SCREENING_ID}/bookings'] = (
        409, {'code': 'not_enough_seats', 'detail': 'Only 1 free seats left'},
    )
    back_to = f'/films/{FILM_ID}?host={HOST_ID}'

    response = stand.client.post(f'/screenings/{SCREENING_ID}/book', data={'seats': 3, 'back_to': back_to})
    page = stand.client.get(response.headers['location'])

    path, query = location(response)
    assert (path, query['host'], query['error']) == (f'/films/{FILM_ID}', [HOST_ID], ['not_enough_seats'])
    assert 'кто-то успел раньше' in page.text


def test_booking_requires_login(stand: Stand):
    """Бронь без входа — ко входу с возвратом, в сервис бронирования запрос не уходит."""
    response = stand.client.post(f'/screenings/{SCREENING_ID}/book', data={'seats': 1})

    assert location(response)[0] == '/login'
    assert not [r for r in stand.booking.requests if r.method == 'POST']


def test_invalid_form_goes_back_not_json(stand: Stand):
    """Неверная форма (0 мест) — назад с подсказкой, а не JSON с ошибками FastAPI."""
    stand.login()

    response = stand.client.post(
        f'/screenings/{SCREENING_ID}/book', data={'seats': 0},
        headers={'Referer': f'http://testserver/screenings/{SCREENING_ID}'},
    )

    assert location(response) == (f'/screenings/{SCREENING_ID}', {'error': ['validation_error']})


def test_create_screening_sends_moscow_time_with_zone(stand: Stand):
    """Время из формы — московское; в API уходит с поясом, чтобы показ не съехал на три часа."""
    stand.login()
    stand.booking.routes['POST /booking/api/v1/screenings'] = (201, SCREENING)

    response = stand.client.post('/screenings', data={
        'film_id': FILM_ID, 'starts_at': '2099-10-17T19:00', 'place': 'Зал 3', 'address': 'Арбат',
        'capacity': 6, 'description': '',
    })

    body = stand.booking.body('POST', '/booking/api/v1/screenings')
    assert body['starts_at'] == '2099-10-17T19:00:00+03:00'
    assert body['description'] is None
    assert location(response) == (f'/screenings/{SCREENING_ID}', {'notice': ['screening_created']})


def test_edit_sends_only_changed_fields(stand: Stand):
    """Правка показа шлёт только изменённые поля: гости не получат письмо о «переносе» на то же время."""
    stand.login(user_id=HOST_ID)
    stand.booking.routes[f'PATCH /booking/api/v1/screenings/{SCREENING_ID}'] = SCREENING

    stand.client.post(f'/screenings/{SCREENING_ID}/edit', data={
        'starts_at': '2099-10-17T19:00', 'place': 'Зал 5', 'address': SCREENING['address'], 'capacity': 6,
    })

    assert stand.booking.body('PATCH', f'/booking/api/v1/screenings/{SCREENING_ID}') == {'place': 'Зал 5'}


def test_screening_page_for_guest_with_booking(stand: Stand):
    """Гость с бронью видит её и может изменить число мест или отменить."""
    stand.login()
    stand.booking.routes[f'GET /booking/api/v1/screenings/{SCREENING_ID}/bookings/mine'] = BOOKING

    text = stand.client.get(f'/screenings/{SCREENING_ID}').text

    assert 'Ваша бронь: 2 места' in text
    assert f'/bookings/{BOOKING["id"]}/cancel' in text


def test_screening_page_for_host_lists_guests(stand: Stand):
    """Хост видит гостей с их рейтингом и кнопки «Изменить» и «Отменить показ»."""
    stand.login(user_id=HOST_ID)
    stand.booking.routes[f'GET /booking/api/v1/screenings/{SCREENING_ID}/bookings'] = [
        {'booking': BOOKING, 'rating': {'average': 5.0, 'votes': 2}},
    ]

    text = stand.client.get(f'/screenings/{SCREENING_ID}').text

    assert 'Тринити' in text and '★ 5,0 · 2 оценки' in text
    assert f'/screenings/{SCREENING_ID}/edit' in text and 'Отменить показ' in text


def test_past_screening_offers_guest_to_rate_host(stand: Stand):
    """После начала показа гость с бронью видит форму оценки хоста."""
    stand.login()
    past = {**SCREENING, 'starts_at': '2020-01-01T16:00:00Z'}
    stand.booking.routes[f'GET /booking/api/v1/screenings/{SCREENING_ID}'] = past
    stand.booking.routes[f'GET /booking/api/v1/screenings/{SCREENING_ID}/bookings/mine'] = BOOKING
    stand.booking.routes[f'GET /booking/api/v1/screenings/{SCREENING_ID}/ratings/mine'] = []
    stand.booking.routes[f'POST /booking/api/v1/screenings/{SCREENING_ID}/ratings'] = (201, {})

    text = stand.client.get(f'/screenings/{SCREENING_ID}').text
    response = stand.client.post(
        f'/screenings/{SCREENING_ID}/rate', data={'target_id': HOST_ID, 'score': 4, 'comment': ' Уютно '},
    )

    assert f'name="target_id" value="{HOST_ID}"' in text
    assert stand.booking.body('POST', f'/booking/api/v1/screenings/{SCREENING_ID}/ratings') == {
        'target_id': HOST_ID, 'score': 4, 'comment': 'Уютно',
    }
    assert location(response)[1] == {'notice': ['rated']}


def test_logout_clears_cookies_even_if_auth_is_down(stand: Stand):
    """Выход удаляет cookie, даже если сервис авторизации не ответил."""
    stand.login()
    stand.auth.down = True

    response = stand.client.post('/logout')

    assert response.headers['location'] == '/'
    assert 'practix_access=""' in response.headers['set-cookie']
