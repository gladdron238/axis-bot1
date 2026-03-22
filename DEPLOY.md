# 🚀 Деплой на Railway — пошаговая инструкция

## Шаг 1 — Регистрация
1. Зайди на https://railway.app
2. Нажми **Start a New Project**
3. Войди через GitHub аккаунт

---

## Шаг 2 — Загрузи код на GitHub

```bash
# В папке axis_bot_railway:
git init
git add .
git commit -m "init: axis agent bot"

# Создай репозиторий на github.com (New repository)
# Затем:
git remote add origin https://github.com/ТВО_ИМЯ/axis-bot.git
git push -u origin main
```

---

## Шаг 3 — Создай проект в Railway

1. На railway.app нажми **New Project**
2. Выбери **Deploy from GitHub repo**
3. Выбери свой репозиторий `axis-bot`
4. Railway автоматически найдёт `nixpacks.toml` и установит зависимости

---

## Шаг 4 — Добавь переменные окружения

1. В проекте нажми на сервис → вкладка **Variables**
2. Добавь две переменные:

| Name | Value |
|------|-------|
| `TELEGRAM_TOKEN` | токен от @BotFather |
| `GEMINI_API_KEY` | ключ от aistudio.google.com |

3. Нажми **Deploy** — Railway перезапустит бота с новыми переменными

---

## Шаг 5 — Проверь логи

1. Вкладка **Deployments** → кликни на последний деплой
2. Открой **Logs**
3. Должна появиться строка:
   ```
   AXIS AGENT BOT started ⚡
   ```

---

## ✅ Готово!

Бот работает 24/7. Railway бесплатно даёт $5 кредитов в месяц —
этого хватит примерно на 500 часов работы лёгкого бота.

---

## 🔄 Обновление бота

Просто сделай `git push` — Railway автоматически передеплоит:

```bash
git add .
git commit -m "update: new feature"
git push
```

---

## ❌ Если бот упал

Проверь логи на вкладке **Deployments**.
Частые причины:
- Неверный `TELEGRAM_TOKEN` → проверь в @BotFather
- Неверный `GEMINI_API_KEY` → проверь на aistudio.google.com
- Превышен лимит Gemini API (бесплатный план: 15 req/min)
