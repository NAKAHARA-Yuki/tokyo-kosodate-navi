FROM python:3.12-slim

WORKDIR /app

# 本番イメージはロックファイルからしか入れない。requirements.in（`>=` 指定）を直接使うと
# ビルドした日によって中身が変わり、prod と staging が別物になる。
# 実際に google-genai が 2.14 → 2.16 に上がって Gemini 呼び出しが 503 になる事故が起きている。
# --require-hashes により、ハッシュの一致しないパッケージは一切インストールされない。
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
