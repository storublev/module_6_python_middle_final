from starlette.types import ASGIApp, Receive, Scope, Send


class TrailingSlashMiddleware:
    """Отбрасывает косую черту в конце пути.

    В ТЗ пути встречаются и со слешем в конце (`/films/<uuid>/`), и без него
    (`/persons/<uuid>`). Маршруты объявлены без слеша, а middleware делает так,
    что оба варианта обслуживает один маршрут — без 307-редиректа и без 404.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] == 'http':
            path = scope['path']
            if len(path) > 1 and path.endswith('/'):
                scope = {**scope, 'path': path.rstrip('/')}
        await self.app(scope, receive, send)
