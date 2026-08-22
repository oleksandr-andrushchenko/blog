FROM public.ecr.aws/sam/build-python3.12

WORKDIR /app
ENV PYTHONPATH=/app:/app/api-lambda
ENV FUNCTION_TEMPLATES_DIR=/app/api-lambda/templates

RUN pip install --no-cache-dir --upgrade pip setuptools wheel

COPY api-lambda/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt uvicorn

COPY shared .
COPY api-lambda ./api-lambda

CMD ["uvicorn", "handler:app", "--host", "0.0.0.0", "--port", "5000", "--reload"]
