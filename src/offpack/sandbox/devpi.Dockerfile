# Зеркало PyPI: root/pypi кэширует каждый скачанный файл в /data/+files/root/pypi/+f.
# Пароль root задаётся случайным при первой инициализации и нигде не сохраняется;
# при перезапуске контейнера инициализация пропускается. Менять пользователей и индексы
# может только root (--restrict-modify), поэтому код из песочницы не может создать свой
# индекс или подменить mirror_url.
FROM python:3.12-slim
RUN pip install --no-cache-dir "devpi-server>=6.10,<7" \
 && useradd --create-home --uid 10002 devpi \
 && mkdir /data && chown devpi:devpi /data
USER devpi
EXPOSE 3141
CMD ["sh", "-ec", "if [ ! -f /data/.serverversion ]; then pw=\"$(python -c 'import secrets; print(secrets.token_hex(24))')\"; devpi-init --serverdir /data --root-passwd \"$pw\"; unset pw; fi; exec devpi-server --serverdir /data --host 0.0.0.0 --port 3141 --restrict-modify root"]
