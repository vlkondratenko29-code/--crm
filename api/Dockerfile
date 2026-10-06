# Build the Mini App
FROM node:22-alpine AS web-build
WORKDIR /web
COPY web/package.json ./
COPY web/package-lock.json* ./
RUN npm install
COPY web/ ./
RUN npm run build

# Run the API and serve the Mini App from the same service
FROM python:3.12-slim
WORKDIR /app
COPY api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY api/app ./app
COPY --from=web-build /web/dist ./web/dist
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
