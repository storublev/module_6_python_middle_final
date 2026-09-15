# Проксирование в сервис авторизации. Подключается в нескольких location.
proxy_pass         http://auth;
proxy_http_version 1.1;
proxy_set_header   Connection "";
proxy_set_header   Host $host;
proxy_set_header   X-Real-IP $remote_addr;
# Перезаписываем, а не дописываем ($proxy_add_x_forwarded_for): uvicorn сервиса
# доверяет заголовку и берёт из него адрес клиента. Дописанный к присланному
# клиентом заголовок позволил бы подставить чужой IP — и обойти лимиты
# попыток по адресу, и записать в историю входов не тот адрес.
proxy_set_header   X-Forwarded-For $remote_addr;
proxy_set_header   X-Forwarded-Proto $scheme;
