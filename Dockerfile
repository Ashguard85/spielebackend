FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/app/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && groupadd -g 10001 app && useradd -u 10001 -g 10001 -d /app app && mkdir -p /app/data && chown -R app:app /app
COPY --chown=app:app app.py ./
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3)"
CMD ["gunicorn","--bind","0.0.0.0:8080","--workers","1","--threads","4","--timeout","40","app:app"]
