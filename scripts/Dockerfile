FROM public.ecr.aws/sam/build-python3.12

WORKDIR /app

RUN dnf install -y zip && dnf clean all

COPY requirements.txt .

RUN pip install -t vendor -r requirements.txt

COPY . .

CMD ["tail", "-f", "/dev/null"]