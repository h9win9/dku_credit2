# 파이썬 3.9 버전이 깔린 가상 컴퓨터를 준비해!
FROM python:3.9-slim

# 작업할 폴더를 /app 으로 정해!
WORKDIR /app

# 쥐니의 부품 명세서(requirements.txt)를 복사해서 부품들을 다 설치해!
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 쥐니의 파이썬 코드(main.py 등)를 전부 복사해!
COPY . .

# 서버를 실행해! (구글이 알아서 포트를 열어줄 거야)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
