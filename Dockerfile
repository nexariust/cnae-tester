FROM python:3.12-alpine

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TESTER_SERVER_URL="" \
    TESTER_TOKEN_FILE=/data/.tester-token

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY evaluator.py ./evaluator.py
COPY tester_eval.py ./tester_eval.py
COPY tester_client.py ./tester_client.py

RUN mkdir -p /data
VOLUME ["/data"]

CMD ["python", "tester_client.py"]