FROM pytorch/pytorch:2.0.1-cuda11.7-cudnn8-runtime

ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/CosyVoice

RUN sed -i s@/archive.ubuntu.com/@/mirrors.aliyun.com/@g /etc/apt/sources.list \
    && apt-get update -y \
    && apt-get install -y --no-install-recommends git git-lfs g++ unzip sox libsox-dev ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && git lfs install

RUN git clone --recursive https://github.com/QwenAudio/CosyVoice.git CosyVoice

# openai-whisper 20231117 still imports pkg_resources while building. Newer
# isolated setuptools releases no longer provide it, so build Whisper once
# against a compatible setuptools version before resolving the full lockfile.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip3 install "setuptools<81" wheel \
    && pip3 install openai-whisper==20231117 --no-build-isolation \
       -i https://mirrors.aliyun.com/pypi/simple/ \
       --trusted-host=mirrors.aliyun.com \
    && cd CosyVoice \
    && pip3 install -r requirements.txt \
       -i https://mirrors.aliyun.com/pypi/simple/ \
       --trusted-host=mirrors.aliyun.com

RUN cd CosyVoice/runtime/python/grpc \
    && python3 -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. cosyvoice.proto

EXPOSE 50000
