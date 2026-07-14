FROM python:3.11-slim
WORKDIR /app

# WeasyPrint + Playwright/Chromium dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 \
      libpangocairo-1.0-0 \
      libcairo2 \
      libgdk-pixbuf-2.0-0 \
      libffi8 \
      shared-mime-info \
      fonts-liberation \
      fonts-noto-core \
      fonts-noto-extra \
      fonts-lohit-deva \
      fontconfig \
      # Chromium runtime deps
      libnss3 \
      libnspr4 \
      libatk1.0-0 \
      libatk-bridge2.0-0 \
      libcups2 \
      libdrm2 \
      libxkbcommon0 \
      libxcomposite1 \
      libxdamage1 \
      libxfixes3 \
      libxrandr2 \
      libgbm1 \
      libasound2 \
      libatspi2.0-0 \
      libx11-6 \
      libxext6 \
      libxss1 \
      libxtst6 \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Install Chromium and all its OS-level dependencies
RUN playwright install chromium && playwright install-deps chromium
COPY . .
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
