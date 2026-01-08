ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:stable
FROM $BUILD_FROM

# Installing dependencies for building Python packages
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
    jq \
    ttf-dejavu

# Installing Python dependencies
COPY requirements.txt /requirements.txt
RUN pip3 install --break-system-packages -r /requirements.txt
RUN apk add --no-cache jq
# Copy the main Python script
COPY display_controller.py /display_controller.py

# Copy the service configuration files
COPY rootfs/ /

# Here we give execution rights to the run file
RUN chmod +x /etc/services.d/display_controller/run
