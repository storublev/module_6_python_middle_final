# Проксирование в сервис авторизации. Подключается в нескольких location.
# Адрес в переменной, чтобы nginx разрешал имя на каждый запрос, — почему
# именно так, объяснено в api.conf.
set $auth http://auth:8000;
proxy_pass         $auth;
proxy_set_header   Host $host;
proxy_set_header   X-Real-IP $remote_addr;
# Перезаписываем, а не дописываем ($proxy_add_x_forwarded_for): uvicorn сервиса
# доверяет заголовку и берёт из него адрес клиента. Дописанный к присланному
# клиентом заголовок позволил бы подставить чужой IP — и обойти лимиты
# попыток по адресу, и записать в историю входов не тот адрес.
proxy_set_header   X-Forwarded-For $remote_addr;
proxy_set_header   X-Forwarded-Proto $scheme;
# Идентификатор запроса генерирует nginx и перезаписывает им присланный
# клиентом: иначе клиент мог бы слить свои запросы с чужими в журналах.
# Тот же идентификатор виден в access-логе nginx и в спанах в Jaeger.
proxy_set_header   X-Request-Id $request_id;
