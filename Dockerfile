FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV IMAGEMAGICK_BINARY=/usr/bin/convert

RUN apt update
RUN apt -y install \
  wget \
  build-essential \
  libssl* \
  libffi-dev \
  libespeak-dev \
  zlib1g-dev \
  libmupdf-dev \
  libfreetype6-dev \
  ffmpeg \
  espeak \
  imagemagick \
  git \
  postgresql \
  postgresql-contrib \
  libfreetype6 \
  libfontconfig1 \
  fonts-liberation

RUN wget https://www.python.org/ftp/python/3.10.14/Python-3.10.14.tgz
RUN tar -xzvf Python-3.10.14.tgz
RUN cd Python-3.10.14
WORKDIR Python-3.10.14
RUN ./configure --enable-optimizations --with-system-ffi
RUN make -j 16
RUN make altinstall

WORKDIR /app

COPY ./requirements.txt .
RUN pip3.10 install numpy
RUN pip3.10 install --no-cache-dir -r requirements.txt

COPY . .
COPY .env /app/.env

COPY ./policy.xml /etc/ImageMagick-6/policy.xml
RUN sed -i 's/<policy domain="path" rights="none" pattern="@\*"/<!--<policy domain="path" rights="none" pattern="@\*"-->/' /etc/ImageMagick-6/policy.xml || true \
    && sed -i 's/<policy domain="path" rights="none" pattern="@\*"/<!--<policy domain="path" rights="none" pattern="@\*"-->/' /etc/ImageMagick-7/policy.xml || true

#     COPY ./fonts /usr/share/fonts/custom
# RUN fc-cache -f -v


EXPOSE 7777
CMD ["bash", "-c", "export $(cat /app/.env | xargs) && python3.10 manage.py migrate && python3.10 manage.py runserver 0.0.0.0:7777"]
