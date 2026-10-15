# CPU image: PP-OCR on ONNX Runtime, no GPU or PyTorch required.
#
#   docker build -t bookable .
#   docker run --rm -v "$PWD/documents:/data/documents:ro" -v "$PWD/master_data:/data/master_data:ro" \
#              -v "$PWD/output:/data/output" bookable
#
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 \
    BOOKABLE_OCR_BACKEND=onnx BOOKABLE_CACHE=/tmp/bookable-cache

RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 # fetch the OCR models at build time so the container runs offline
 && python -c "from rapidocr import RapidOCR; RapidOCR(params={'Global.log_level': 'error'})"

COPY erp.py run.py ./
COPY bookable ./bookable
COPY tools ./tools
COPY tests ./tests

ENTRYPOINT ["python", "run.py"]
CMD ["/data/documents", "--out", "/data/output", "--masters", "/data/master_data"]
