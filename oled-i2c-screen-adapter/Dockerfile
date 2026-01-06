ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:stable
FROM $BUILD_FROM

# Installer les dépendances système et Python nécessaires
RUN apk add --no-cache \
    python3 \
    py3-pip \
    python3-dev \
    build-base \
    libffi-dev \
    freetype-dev \
    jpeg-dev \
    linux-headers \
    libjpeg \
    bash \
    jq

# Installer les dépendances Python
COPY requirements.txt /requirements.txt
RUN pip3 install --break-system-packages -r /requirements.txt
RUN apk add --no-cache jq
# Copier le script principal Python
COPY display_controller.py /display_controller.py

# Copier les fichiers de configuration du service
COPY rootfs/ /

#here we give execution rights to the run file
RUN chmod +x /etc/services.d/display_controller/run
