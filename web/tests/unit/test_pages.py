"""Страницы каталога и карточки фильма: что видит зритель и откуда это берётся."""

from http import HTTPStatus

from tests.unit.conftest import FILM, FILM_ID, HOST_ID, SCREENING_ID, SERIES_ID, Stand


def test_catalog_shows_posters_and_placeholders(stand: Stand):
    """Каталог — сетка обложек; у фильма без обложки — заглушка с названием, а не пустое место."""
    response = stand.client.get('/')

    assert response.status_code == HTTPStatus.OK
    assert f'src="{FILM["poster_url"]}"' in response.text
    assert 'poster--empty' in response.text and 'Star Trek' in response.text
    assert f'href="/films/{FILM_ID}"' in response.text


def test_catalog_search_and_genre_go_to_catalog_api(stand: Stand):
    """Поиск идёт в /films/search, фильтр жанра — в список с параметром genre."""
    stand.catalog.routes['GET /api/v1/films/search'] = [FILM]
    genre = FILM['genre'][0]['uuid']

    stand.client.get('/', params={'q': 'star'})
    stand.client.get('/', params={'genre': genre})

    assert stand.catalog.last('GET', '/api/v1/films/search').url.params['query'] == 'star'
    assert stand.catalog.last('GET', '/api/v1/films').url.params['genre'] == genre


def test_catalog_down_is_friendly_503(stand: Stand):
    """Каталог не отвечает — страница «временно недоступен», а не трейсбек."""
    stand.catalog.down = True

    response = stand.client.get('/')

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert 'временно недоступен' in response.text


def test_request_id_goes_to_every_backend(stand: Stand):
    """X-Request-Id страницы уходит во все API: по нему страница собирается в журналах и Jaeger."""
    stand.client.get(f'/films/{FILM_ID}')

    ids = {r.headers['x-request-id'] for r in stand.catalog.requests + stand.booking.requests}
    assert ids == {'req-test'}


def test_film_card_like_imdb(stand: Stand):
    """Карточка: обложка, рейтинг, жанры, создатели ссылками и кнопка «Купить билет»."""
    response = stand.client.get(f'/films/{FILM_ID}')

    assert response.status_code == HTTPStatus.OK
    text = response.text
    assert 'poster--large' in text and '8.6' in text
    assert f'/persons/{FILM["directors"][0]["uuid"]}' in text
    assert f'/?genre={FILM["genre"][0]["uuid"]}' in text
    assert 'https://www.imdb.com/title/tt0076759/' in text
    assert 'Купить билет' in text


def test_film_card_lists_hosts_with_rating(stand: Stand):
    """Шаг 1 — хосты фильма с рейтингом и ссылкой «Выбрать»."""
    text = stand.client.get(f'/films/{FILM_ID}').text

    assert 'Нео' in text and '★ 4,7 · 3 оценки' in text
    assert f'/films/{FILM_ID}?host={HOST_ID}#tickets' in text


def test_selected_host_shows_dates_and_booking_form(stand: Stand):
    """Шаг 2 и 3 — даты выбранного хоста и форма брони для вошедшего зрителя."""
    stand.login()

    text = stand.client.get(f'/films/{FILM_ID}', params={'host': HOST_ID}).text

    request = stand.booking.last('GET', '/booking/api/v1/screenings')
    assert (request.url.params['film_id'], request.url.params['host_id']) == (FILM_ID, HOST_ID)
    assert f'action="/screenings/{SCREENING_ID}/book"' in text
    assert 'сб, 17 окт, 19:00' in text  # 16:00 UTC — это 19:00 по Москве


def test_anonymous_is_asked_to_log_in_to_book(stand: Stand):
    """Гостю без входа вместо формы — «Войти и забронировать» с возвратом на эту же карточку."""
    text = stand.client.get(f'/films/{FILM_ID}', params={'host': HOST_ID}).text

    assert 'Войти и забронировать' in text
    assert f'action="/screenings/{SCREENING_ID}/book"' not in text


def test_series_has_no_tickets(stand: Stand):
    """У сериала нет кнопки «Купить билет», и в сервис бронирования за хостами не ходят зря."""
    text = stand.client.get(f'/films/{SERIES_ID}').text

    assert 'Купить билет' not in text
    assert 'только на полнометражные' in text


def test_booking_outage_keeps_film_card(stand: Stand):
    """Сервис бронирования лежит — карточка фильма открывается, блок брони говорит «временно недоступно»."""
    stand.booking.down = True

    response = stand.client.get(f'/films/{FILM_ID}')

    assert response.status_code == HTTPStatus.OK
    assert 'Star Wars' in response.text and 'Бронирование временно недоступно' in response.text


def test_unknown_film_is_404_page(stand: Stand):
    """Несуществующий фильм — страница 404, а не 500."""
    response = stand.client.get('/films/7e1b5f0a-0000-4000-8000-000000000000')

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert 'Фильм не найден' in response.text


def test_unknown_path_is_404_page(stand: Stand):
    """Неизвестный адрес и кривой UUID — страница 404 в оформлении сайта."""
    assert 'Страница не найдена' in stand.client.get('/no-such-page').text
    assert stand.client.get('/films/not-a-uuid').status_code == HTTPStatus.NOT_FOUND


def test_afisha_and_host_page(stand: Stand):
    """Афиша — все будущие показы; страница хоста — рейтинг, показы и отзывы."""
    stand.booking.routes[f'GET /booking/api/v1/users/{HOST_ID}/rating'] = {
        'user_id': HOST_ID, 'name': 'Нео', 'as_host': {'average': 5.0, 'votes': 1},
        'as_guest': {'average': None, 'votes': 0},
    }
    stand.booking.routes[f'GET /booking/api/v1/users/{HOST_ID}/reviews'] = {
        'items': [{'id': '1', 'screening_id': SCREENING_ID, 'author_id': '2', 'author_name': 'Тринити',
                   'target_id': HOST_ID, 'target_role': 'host', 'score': 5, 'comment': 'Уютно',
                   'created_at': '2026-10-02T10:00:00Z'}],
        'total': 1, 'page_number': 1, 'page_size': 20,
    }

    afisha = stand.client.get('/afisha').text
    host = stand.client.get(f'/hosts/{HOST_ID}').text

    assert f'/screenings/{SCREENING_ID}' in afisha and 'свободно 4 места' in afisha
    assert '★ 5,0 · 1 оценка' in host and 'Уютно' in host


def test_health_needs_no_request_id(stand: Stand):
    """Проверка живости контейнера ходит мимо nginx — без X-Request-Id."""
    stand.client.headers.pop('X-Request-Id')

    assert stand.client.get('/health').status_code == HTTPStatus.OK
    assert stand.client.get('/').status_code == HTTPStatus.BAD_REQUEST


def test_film_card_in_russian_from_kinopoisk(stand: Stand):
    """Карточка по-русски: название и описание Кинопоиска, оригинал с годом, рейтинг КП и ссылка."""
    text = stand.client.get(f'/films/{FILM_ID}').text

    assert '<h1 class="film-hero__title">Звёздные войны</h1>' in text
    assert 'Star Wars · 1977' in text and 'КП 8.1' in text
    assert 'Давным-давно в далёкой галактике' in text and 'Описание на английском' in text
    assert 'https://www.kinopoisk.ru/film/333/' in text


def test_catalog_tile_shows_russian_and_original_title(stand: Stand):
    """В сетке каталога — русское название и под ним оригинальное; обложка из базы каталога."""
    text = stand.client.get('/').text

    assert 'Звёздные войны' in text and 'tile__original">Star Wars' in text
    assert f'src="/posters/{FILM_ID}.jpg"' in text
