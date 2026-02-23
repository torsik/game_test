# Безопасное приложение для проверки кодов. Коды хранятся в SQLite на сервере — в HTML ничего не попадает.

Структура

- app/ (main.py / requirements.txt / Dockerfile templates/ (index.html  # Страница для пользователей / admin.html  # Админка)
- nginx/ (nginx.conf / default.conf certs/   - # сюда кладём cert.pem + key.pem)
- docker-compose.yml
- generate_cert.sh
- .env.example

# Запуск на домашнем сервере

# 1. Подготовка
   
bashcp .env.example .env
nano .env   # задай свой ADMIN_KEY

# 2. Сгенерируй SSL-сертификат
   
bashchmod +x generate_cert.sh
./generate_cert.sh

# 3. Запуск
   
bashdocker compose up -d --build

Приложение доступно:

https://localhost       — страница для пользователей
https://localhost/admin — админка

Браузер покажет предупреждение о self-signed сертификате — нажми "Advanced → Proceed".

Управление кодами (Админка)
Открой https://localhost/admin, введи ADMIN_KEY — и управляй кодами прямо в браузере.
Или через curl:
bash# Добавить

curl -k -X POST https://localhost/api/admin/codes \
  -H "Content-Type: application/json" \
  -H "x-admin-key: ВАШ_КЛЮЧ" \
  -d '{"code": "NEW-CODE", "message": "Секретное сообщение"}'

Список
curl -k https://localhost/api/admin/codes -H "x-admin-key: ВАШ_КЛЮЧ"

Удалить (по id)
curl -k -X DELETE https://localhost/api/admin/codes/1 -H "x-admin-key: ВАШ_КЛЮЧ"

Если были изменения: docker compose up -d --build

