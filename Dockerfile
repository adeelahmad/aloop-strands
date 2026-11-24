# aloop-strands: supervisor + dashboard, with Strands Box inside the image for workers.
# Box is built from a pinned source revision (as Box's EKS example does); no secrets in the image.
FROM python:3.13-slim-bookworm AS box-build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential cmake pkg-config libssl-dev git curl ca-certificates perl \
 && rm -rf /var/lib/apt/lists/*
ARG BOX_REV=8c26868a952e954ba1b99b7d7bf46b67da94f4c5
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o /tmp/rustup-init.sh && sh /tmp/rustup-init.sh -y --profile minimal --default-toolchain none
ENV PATH="/root/.cargo/bin:${PATH}"
RUN git init /src && git -C /src remote add origin https://github.com/strands-agents/box.git \
 && git -C /src fetch --depth 1 origin ${BOX_REV} && git -C /src checkout --detach FETCH_HEAD \
 && test "$(git -C /src rev-parse HEAD)" = "${BOX_REV}"
WORKDIR /src
RUN cargo build --locked --release -p strands-box -p strands-box-containment \
 && mkdir -p /out && cp target/release/strands-box /out/box \
 && cp target/release/strands-box-sock-alias target/release/strands-box-contain-trampoline /out/

FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/* \
 && groupadd -g 991 agent && useradd -u 991 -g 991 -m agent
COPY --from=box-build /out/ /opt/box/
WORKDIR /opt/aloop-strands
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && python -c "import aloop_strands"
ENV ALOOP_BOX_BIN=/opt/box/box ALOOP_BOX_STATE=/tmp/aloop-box HOME=/home/agent
USER 991:991
WORKDIR /work
EXPOSE 8787
ENTRYPOINT ["aloop-strands"]
CMD ["--help"]
