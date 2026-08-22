FROM public.ecr.aws/sam/build-python3.12

WORKDIR /app

RUN dnf install -y zip && dnf clean all

COPY requirements.txt .

RUN pip install -t /opt/vendor -r requirements.txt

ENV PYTHONPATH=/opt/vendor:/app/scripts:/app/shared:/app/web-lambda:/app/api-lambda

COPY . .

CMD ["tail", "-f", "/dev/null"]