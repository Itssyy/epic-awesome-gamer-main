# Epic Games Free Games Collector

Автоматический сборщик бесплатных игр из Epic Games Store. Скрипт автоматически собирает бесплатные игры для указанных аккаунтов.

## Возможности

- Автоматический сбор бесплатных игр
- Поддержка нескольких аккаунтов
- Автоматический запуск через GitHub Actions
- Обработка капчи
- Логирование процесса

## Установка

1. Клонируйте репозиторий:
```bash
git clone https://github.com/ваш-username/epic-games-collector.git
cd epic-games-collector
```

2. Установите зависимости:
```bash
pip install -r requirements.txt
```

## Настройка

1. Создайте файл `accounts.json` со следующей структурой:
```json
[
    {
        "email": "ваш_email@example.com",
        "password": "ваш_пароль"
    }
]
```

2. Для запуска через GitHub Actions:
   - Перейдите в Settings -> Secrets and variables -> Actions
   - Создайте секрет `EPIC_ACCOUNTS` с содержимым файла accounts.json

## Использование

### Локальный запуск
```bash
python main.py
```

### GitHub Actions
Скрипт автоматически запускается каждое воскресенье в 00:00 UTC.

## Лицензия

MIT 