FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./

ENV DB_PATH=/data/feedback.db
VOLUME /data
CMD ["python", "-u", "bot.py"]
