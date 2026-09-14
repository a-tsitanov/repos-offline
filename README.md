# offpack

Перенос npm- и PyPI-пакетов в изолированный контур с Nexus Repository.

На онлайн-машине `offpack build` выполняет команду установки в docker-песочнице,
собирает все скачанные пакеты под нужные платформы и упаковывает их в подписанный архив.
В офлайне `offpack import` проверяет архив и загружает пакеты в Nexus.

## Требования

- Онлайн-машина: Python ≥ 3.12, Docker с compose v2, OpenSSH ≥ 8.1 (`ssh-keygen`), интернет.
- Офлайн-машина с доступом к Nexus: Python ≥ 3.12, OpenSSH ≥ 8.1. Больше ничего ставить не
  нужно — у offpack нет зависимостей.

Подпись и проверка идут через `ssh-keygen -Y`, он появился в OpenSSH 8.1. Во встроенном
OpenSSH старых сборок Windows 10 (7.7) его нет: проверьте `ssh -V` и при необходимости
обновите OpenSSH.

## Установка

```bash
uv tool install .            # на онлайн-машине из исходников
uv build                     # wheel для переноса: dist/offpack-0.1.0-py3-none-any.whl
pip install offpack-0.1.0-py3-none-any.whl   # в офлайне
```

## Ключ подписи

Один раз на онлайн-машине:

```bash
mkdir -p ~/.config/offpack
ssh-keygen -t ed25519 -N "" -C offpack -f ~/.config/offpack/signing_key
printf 'offpack %s\n' "$(cut -d' ' -f1,2 ~/.config/offpack/signing_key.pub)" > allowed_signers
```

Файл `allowed_signers` перенести в офлайн. Приватный ключ никуда не переносить.

Пути можно не указывать в каждой команде: `build` берёт ключ из `$OFFPACK_SIGN_KEY`
(иначе `~/.config/offpack/signing_key`), `import` — файл из `$OFFPACK_ALLOWED_SIGNERS`,
если не заданы `--allowed-signers` и `--allow-unsigned`.

## Сборка (онлайн)

```bash
offpack build -- npx @deepseek-ai/dsh web
offpack build -- uv tool install graphify
offpack build --platform win-x64 --python 3.11,3.12 -- pip install requests
```

Поддерживаются `npx`, `npm install|i|exec`, `uvx`, `uv tool install|run`, `uv pip install`,
`pip install`, `python -m pip install` со спецификациями пакетов. Инструмент не запускается,
только устанавливается. Результат: `dist/<имя>-<время>.tar.gz`, рядом лог `.log`.
Перед переносом прочитайте отчёт в конце вывода (он же `report.txt` в архиве):

- Список пакетов. Проверьте, что в нём нет неожиданных имён, особенно похожих на внутренние
  (scope вашей организации, внутренние названия). Код в песочнице может заставить прокси
  скачать любой публичный пакет, и он попадёт в архив; публичный `@corp/utils` в Nexus
  подменит внутренний пакет с тем же именем (dependency confusion). Такой архив не переносите.
- `egress` — загрузки не из реестров, в Nexus они не попадут.
- `egress_log_missing` — лог прокси не получен, внешние загрузки не проверены.
- `sdist_only` — на клиенте нужна сборка из исходников.

Опции: `--platform linux-x64,win-x64`, `--python 3.12`, `--out dist`, `--sign-key PATH` или
`--no-sign`, `--timeout 900`, `--keep` (оставить стек для отладки), `-v`/`--verbose`.

### Что видно во время сборки

Каждый этап печатается сразу, с таймером от начала сборки и длительностью этапа:

```
[00:00] поднимаю песочницу (docker compose up --build)…
[00:03] ✓ песочница поднята (3s)
[00:03] жду готовности verdaccio, devpi, squid…
[00:06] ✓ сервисы готовы (3s)
[00:06] проход npm: pnpm add @deepseek-ai/dsh --os=current --os=linux --os=win32 --cpu=current …
        ↓ npm  @deepseek-ai/dsh 0.1.5-rc.1  16.2 КБ
        ↓ npm  commander 15.0.0  51.5 КБ
        …
[00:42] ✓ npm: +516 файлов, 83.4 МБ (35s)
[00:42] копирую файлы из хранилищ прокси…
[00:55] ✓ файлы собраны (13s)
…
[00:58] npm 516 файлов 83.4 МБ · pypi 0 файлов 0 Б · предупреждений 0
```

- Строки `↓` — файлы пакетов, которые прокси (Verdaccio, devpi) скачали в этой сборке, каждый
  один раз. Размер показан для npm; devpi размер в лог не пишет, поэтому у PyPI-файлов его нет.
- После прохода — сколько новых файлов он скачал. При ошибке: `✗ <проход>: код N (время), лог: …`.
- Лог `dist/<имя>-<время>.log` пишется по ходу проходов: заголовок с временем и командой,
  весь вывод pnpm/uv, код выхода и длительность.
- `-v` дополнительно выводит в терминал вывод pnpm/uv по мере поступления.

### Проходы в песочнице

- npm — один проход `pnpm add` в пустом проекте под платформу песочницы и все целевые платформы
  сразу (`--os`/`--cpu`), установочные скрипты включены. pnpm используется только внутри
  песочницы: клиенты в офлайне по-прежнему ставят пакеты через `npm`/`npx` из Nexus.
- PyPI — разведка `uv pip install` и по проходу на каждую платформу и версию Python.

## Импорт (офлайн)

```bash
export NEXUS_USER=deployer NEXUS_PASSWORD=...
offpack import dsh-20260913-214000.tar.gz --nexus http://nexus:8081 --allowed-signers allowed_signers
offpack import archive.tar.gz --nexus http://nexus:8081 --allowed-signers allowed_signers --dry-run
```

Опции: `--npm-repo npm-hosted`, `--pypi-repo pypi-hosted`, `--allow-unsigned`.
Уже имеющиеся в Nexus файлы пропускаются.

## Коды выхода

| Код | Значение |
|---|---|
| 0 | успешно |
| 1 | `import`: были ошибки загрузки отдельных файлов |
| 2 | ошибка offpack: неверные аргументы, сборка не удалась, архив или подпись не прошли проверку, Nexus недоступен, ошибка файловой системы |
| 130 | прервано (Ctrl+C) |

## Настройка Nexus

- Community Edition: принять EULA (мастер первого входа), иначе загрузка отвечает 403.
- Hosted-репозитории `npm-hosted` и `pypi-hosted` (Deployment policy: Disable redeploy).
- Group-репозитории `npm-all` и `pypi-all`, включающие hosted.
- Роль для пользователя импорта с привилегиями:
  - `nx-component-upload`;
  - `nx-repository-view-npm-npm-hosted-browse`, `-read`, `-add`, `-edit`;
  - `nx-repository-view-pypi-pypi-hosted-browse`, `-read`, `-add`, `-edit`.

  Для других имён репозиториев (`--npm-repo`, `--pypi-repo`) подставьте их в имена привилегий.
- Клиентам нужно чтение из `npm-all` и `pypi-all`: либо включите анонимный доступ
  (Security → Anonymous Access; у роли пользователя anonymous должны быть
  `nx-repository-view-npm-npm-all-browse`/`-read` и `nx-repository-view-pypi-pypi-all-browse`/`-read`,
  стандартная `nx-anonymous` даёт чтение всех репозиториев), либо выдайте клиентам
  учётные данные.

## Настройка клиентов

Linux:
```bash
npm config set registry http://nexus:8081/repository/npm-all/
export UV_DEFAULT_INDEX=http://nexus:8081/repository/pypi-all/simple
export PIP_INDEX_URL=http://nexus:8081/repository/pypi-all/simple PIP_TRUSTED_HOST=nexus
export UV_PYTHON_DOWNLOADS=never
```

Windows (PowerShell):
```powershell
npm config set registry http://nexus:8081/repository/npm-all/
[Environment]::SetEnvironmentVariable("UV_DEFAULT_INDEX", "http://nexus:8081/repository/pypi-all/simple", "User")
[Environment]::SetEnvironmentVariable("PIP_INDEX_URL", "http://nexus:8081/repository/pypi-all/simple", "User")
[Environment]::SetEnvironmentVariable("PIP_TRUSTED_HOST", "nexus", "User")
[Environment]::SetEnvironmentVariable("UV_PYTHON_DOWNLOADS", "never", "User")
```

Node и Python на клиентах должны быть установлены заранее: offpack их не переносит.

## Ограничения v1

Не поддерживаются: установки по проекту (`npm ci`, `uv sync`, `pip install -r`), пакеты из git,
по URL и локальным путям, команды pnpm/yarn/bun (выполняются как есть, только под платформу
песочницы), платформы arm64 и macOS, сканирование уязвимостей,
перенос бинарников, которые `postinstall` скачивает не из реестров.

## Разработка

```bash
uv sync
uv run pytest                 # unit-тесты
uv run pytest -m docker -v    # интеграция с docker и интернетом
uv run pytest -m nexus -v     # e2e с контейнером Nexus
uv run ruff check
```
