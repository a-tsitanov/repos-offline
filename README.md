# offpack

Перенос npm- и PyPI-пакетов в изолированный контур с Nexus Repository.

На онлайн-машине `offpack build` выполняет команду установки в docker-песочнице,
собирает все скачанные пакеты под нужные платформы и упаковывает их в подписанный архив.
В офлайне `offpack import` проверяет архив и загружает пакеты в Nexus.

## Требования

- Онлайн-машина: Python ≥ 3.12, Docker с compose v2, OpenSSH (`ssh-keygen`), интернет.
- Офлайн-машина с доступом к Nexus: Python ≥ 3.12, OpenSSH. Больше ничего ставить не нужно —
  у offpack нет зависимостей.

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

## Сборка (онлайн)

```bash
offpack build -- npx @deepseek-ai/dsh web
offpack build -- uv tool install graphify
offpack build --platform win-x64 --python 3.11,3.12 -- pip install requests
```

Поддерживаются `npx`, `npm install|i|exec`, `uvx`, `uv tool install|run`, `uv pip install`,
`pip install`, `python -m pip install` со спецификациями пакетов. Инструмент не запускается,
только устанавливается. Результат: `dist/<имя>-<время>.tar.gz`, рядом лог `.log`.
Перед переносом прочитайте отчёт в конце вывода: предупреждения `egress` означают загрузки
не из реестров (в Nexus они не попадут), `sdist_only` — на клиенте нужна сборка из исходников.

Опции: `--platform linux-x64,win-x64`, `--python 3.12`, `--out dist`, `--sign-key PATH` или
`--no-sign`, `--timeout 900`, `--keep` (оставить стек для отладки).

## Импорт (офлайн)

```bash
export NEXUS_USER=deployer NEXUS_PASSWORD=...
offpack import dsh-20260913-214000.tar.gz --nexus http://nexus:8081 --allowed-signers allowed_signers
offpack import archive.tar.gz --nexus http://nexus:8081 --allowed-signers allowed_signers --dry-run
```

Опции: `--npm-repo npm-hosted`, `--pypi-repo pypi-hosted`, `--allow-unsigned`.
Уже имеющиеся в Nexus файлы пропускаются. Код выхода 1 — были ошибки загрузки.

## Настройка Nexus

- Community Edition: принять EULA (мастер первого входа), иначе загрузка отвечает 403.
- Hosted-репозитории `npm-hosted` и `pypi-hosted` (Deployment policy: Disable redeploy).
- Group-репозитории `npm-all` и `pypi-all`, включающие hosted.
- Пользователь для импорта с правами `nx-component-upload` и `nx-repository-view-*-*-edit`
  на оба hosted-репозитория.

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
по URL и локальным путям, pnpm/yarn/bun, платформы arm64 и macOS, сканирование уязвимостей,
перенос бинарников, которые `postinstall` скачивает не из реестров.

## Разработка

```bash
uv sync
uv run pytest                 # unit-тесты
uv run pytest -m docker -v    # интеграция с docker и интернетом
uv run pytest -m nexus -v     # e2e с контейнером Nexus
uv run ruff check
```
