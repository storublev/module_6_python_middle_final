# Проксирование в сервис сбора пользовательских действий. Подключается в
# нескольких location. Адрес в переменной, чтобы nginx разрешал имя на каждый
# запрос, — почему именно так, объяснено в api.conf.
set $ugc http://ugc:8000;
proxy_pass         $ugc;
proxy_set_header   Host $host;
proxy_set_header   X-Real-IP $remote_addr;
proxy_set_header   X-Forwarded-For $remote_addr;
proxy_set_header   X-Forwarded-Proto $scheme;
# Идентификатор запроса генерирует nginx и перезаписывает им присланный
# клиентом: иначе клиент мог бы слить свои запросы с чужими в журналах.
proxy_set_header   X-Request-Id $request_id;
