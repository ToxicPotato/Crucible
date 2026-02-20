FROM python:3.10-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir fastapi uvicorn[standard] python-dotenv httpx pydantic

# Copy backend source
COPY backend/ ./backend/

# Create data directory for conversation storage
RUN mkdir -p data/conversations

EXPOSE 8001

CMD ["python", "-m", "backend.main"]
