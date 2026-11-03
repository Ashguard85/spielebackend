FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt
COPY app.py /app/app.py
EXPOSE 8080
CMD ["gunicorn","--workers","2","--bind","0.0.0.0:8080","--timeout","90","app:app"]
