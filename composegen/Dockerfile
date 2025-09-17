FROM python:3.11-slim
WORKDIR /app
# Install omegaconf and pyyaml for config parsing
RUN pip install omegaconf pyyaml
# Create and declare /config as a volume for mounting the host config folder
RUN mkdir /config
VOLUME ["/config"]
# Only copy the generator script; config will be mounted at runtime
COPY generate_compose.py .
# Inform users: /config must be mounted at runtime
ENTRYPOINT ["python", "generate_compose.py"]