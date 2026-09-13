# Зеркало PyPI: root/pypi кэширует каждый скачанный файл в /data/+files.
FROM python:3.12-slim
RUN pip install --no-cache-dir "devpi-server>=6.10,<7" \
 && useradd --create-home --uid 10002 devpi \
 && mkdir /data && chown devpi:devpi /data
USER devpi
EXPOSE 3141
CMD ["sh", "-c", "devpi-init --serverdir /data >/dev/null 2>&1 || true; exec devpi-server --serverdir /data --host 0.0.0.0 --port 3141"]
