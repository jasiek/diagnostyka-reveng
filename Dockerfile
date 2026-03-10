FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Base tools + blutter build dependencies
RUN apt-get update && apt-get install -y \
    unzip \
    zip \
    wget \
    curl \
    openjdk-17-jdk \
    python3 \
    python3-pip \
    python3-venv \
    git \
    jq \
    cmake \
    ninja-build \
    build-essential \
    pkg-config \
    libicu-dev \
    libcapstone-dev \
    g++-13 \
    && rm -rf /var/lib/apt/lists/*

# Ensure g++-13 is the default
RUN update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-13 100

# jadx - Java/Android decompiler (decompiles DEX/APK to Java source)
RUN wget -q https://github.com/skylot/jadx/releases/download/v1.5.1/jadx-1.5.1.zip -O /tmp/jadx.zip \
    && mkdir -p /opt/jadx \
    && unzip -q /tmp/jadx.zip -d /opt/jadx \
    && chmod +x /opt/jadx/bin/* \
    && rm /tmp/jadx.zip
ENV PATH="/opt/jadx/bin:${PATH}"

# apktool - APK resource decoder (decodes resources, manifests, smali)
RUN wget -q https://raw.githubusercontent.com/iBotPeaches/Apktool/master/scripts/linux/apktool -O /usr/local/bin/apktool \
    && chmod +x /usr/local/bin/apktool \
    && wget -q https://bitbucket.org/ApertureDevelopment/ApertureDevel-Releases/downloads/apktool_2.10.0.jar -O /usr/local/bin/apktool.jar \
    || wget -q https://github.com/ApertureDevelopment/apktool/releases/latest/download/apktool_2.10.0.jar -O /usr/local/bin/apktool.jar \
    || true

# dex2jar - converts DEX to JAR for analysis
RUN wget -q https://github.com/pxb1988/dex2jar/releases/download/v2.4/dex-tools-v2.4.zip -O /tmp/dex2jar.zip \
    && mkdir -p /opt/dex2jar \
    && unzip -q /tmp/dex2jar.zip -d /opt/dex2jar \
    && chmod +x /opt/dex2jar/dex-tools-v2.4/*.sh \
    && rm /tmp/dex2jar.zip
ENV PATH="/opt/dex2jar/dex-tools-v2.4:${PATH}"

# blutter - Dart AOT snapshot analyzer (extracts class definitions, method signatures from Flutter libapp.so)
RUN git clone https://github.com/worawit/blutter.git /opt/blutter \
    && cd /opt/blutter \
    && python3 -m venv venv \
    && . venv/bin/activate \
    && pip install pyelftools requests
ENV PATH="/opt/blutter:${PATH}"

WORKDIR /workspace

CMD ["/bin/bash"]
