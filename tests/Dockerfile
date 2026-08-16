FROM public.ecr.aws/sam/build-python3.12

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip setuptools wheel

COPY api-lambda/requirements.txt /tmp/api-requirements.txt
COPY web-lambda/requirements.txt /tmp/web-requirements.txt
COPY scripts/requirements.txt /tmp/test-requirements.txt
RUN pip install --no-cache-dir \
    -r /tmp/api-requirements.txt \
    -r /tmp/web-requirements.txt \
    -r /tmp/test-requirements.txt \
    uvicorn

ENV PYTHONPATH=/app:/api-lambda:/web-lambda:/tests:/scripts
ENV FUNCTION_TEMPLATES_DIR=/web-lambda/templates:/api-lambda/templates
