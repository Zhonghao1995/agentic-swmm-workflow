FROM python:3.11-slim-bookworm

ARG AGENTIC_SWMM_REPO=https://github.com/Zhonghao1995/agentic-swmm-workflow.git
ARG AGENTIC_SWMM_REF=v0.6.3a1
ARG SWMM_REF=v5.2.4

LABEL org.opencontainers.image.title="Agentic SWMM Workflow"
LABEL org.opencontainers.image.description="Reproducible container environment for Agentic SWMM deterministic workflows"
LABEL org.opencontainers.image.source="https://github.com/Zhonghao1995/agentic-swmm-workflow"
LABEL org.opencontainers.image.version="${AGENTIC_SWMM_REF}"

ENV PYTHONUNBUFFERED=1
ENV MPLBACKEND=Agg
ENV PATH="/usr/local/bin:${PATH}"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      build-essential \
      ca-certificates \
      cmake \
      git \
      nodejs \
      npm \
    && rm -rf /var/lib/apt/lists/*

RUN git init /tmp/swmm-src \
    && git -C /tmp/swmm-src remote add origin https://github.com/USEPA/Stormwater-Management-Model.git \
    && git -C /tmp/swmm-src fetch --depth 1 origin "${SWMM_REF}" \
    && git -C /tmp/swmm-src checkout --detach FETCH_HEAD \
    && cmake -S /tmp/swmm-src -B /tmp/swmm-build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /tmp/swmm-build --config Release -j "$(nproc)" \
    && find /tmp/swmm-build -type f -name runswmm -print -quit \
      | xargs -I{} cp {} /usr/local/bin/runswmm \
    && find /tmp/swmm-build -type f \( -name 'libswmm5.so*' -o -name 'libswmm-output.so*' \) \
      -exec cp {} /usr/local/lib/ \; \
    && ldconfig \
    && ln -sf /usr/local/bin/runswmm /usr/local/bin/swmm5 \
    && swmm5 --version \
    && rm -rf /tmp/swmm-src /tmp/swmm-build

RUN git init /app \
    && git -C /app remote add origin "${AGENTIC_SWMM_REPO}" \
    && git -C /app fetch --depth 1 origin "${AGENTIC_SWMM_REF}" \
    && git -C /app checkout --detach FETCH_HEAD

RUN python -m pip install --no-cache-dir --upgrade pip \
    && if [ -f requirements.txt ]; then \
         python -m pip install --no-cache-dir -r requirements.txt; \
       fi \
    && if [ -f scripts/requirements.txt ]; then \
         python -m pip install --no-cache-dir -r scripts/requirements.txt; \
       fi

RUN set -eux; \
    find /app/mcp -mindepth 2 -maxdepth 2 -type f -name package.json -print | sort | while read -r package_json; do \
      mcp_dir="$(dirname "$package_json")"; \
      if [ -f "$mcp_dir/package-lock.json" ]; then \
        npm --prefix "$mcp_dir" ci --omit=dev; \
      else \
        npm --prefix "$mcp_dir" install --omit=dev; \
      fi; \
    done

COPY scripts/docker_entrypoint.sh /usr/local/bin/agentic-swmm
RUN chmod +x /usr/local/bin/agentic-swmm \
    && mkdir -p /app/runs

ENTRYPOINT ["agentic-swmm"]
CMD ["acceptance"]
