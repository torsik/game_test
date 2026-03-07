# Code Lookup

Secure code → message/file lookup app.

## Structure
```
├── app/
│   ├── main.py
│   ├── requirements.txt
│   ├── Dockerfile
│   └── templates/
│       ├── index.html   # User page (EN/RU switcher)
│       └── admin.html   # Admin panel
├── nginx/
│   ├── nginx.conf
│   └── default.conf
├── docker-compose.yml
├── .env.example
└── README.md
```

## Setup & run

```bash
# 1. Set your passwords
cp .env.example .env
nano .env

# 2. Create logs directory
mkdir -p logs/nginx

# 3. Start
docker compose up -d --build
```

## Environment variables

| Variable           | Default     | Description                        |
|--------------------|-------------|------------------------------------|
| ADMIN_KEY          | changeme    | API key for admin endpoints        |
| ADMIN_UI_PASS      | adminpass   | Password for /admin UI             |
| RATE_LIMIT_ATTEMPTS| 10          | Max attempts per IP per window     |
| RATE_LIMIT_WINDOW  | 60          | Rate limit window in seconds       |
| LOG_PATH           | /data/app.log | Path to application log          |
| UPLOAD_DIR         | /data/uploads | Path to uploaded files           |

## View logs

```bash
# App logs (codes entered, admin actions)
docker exec -it codelookup_app tail -f /data/app.log

# nginx access log
tail -f logs/nginx/access.log

# Live docker logs
docker compose logs -f
```

## Move to another server

Data is stored in Docker volume `app_data`.
To migrate: `docker run --rm -v app_data:/data -v $(pwd):/backup alpine tar czf /backup/data.tar.gz /data`
Then restore on new server.
