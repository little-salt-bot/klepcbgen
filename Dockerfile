FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY klepcbgen.py klepcbgenmod.py firmware.py webui.py render.py ./
COPY templates ./templates

# CLI entrypoint: generate a board from a mounted KLE JSON.
#   docker run -v $(pwd)/out:/out -v $(pwd)/layout.json:/in/layout.json \
#     klepcbgen -o /out/mykb -m /out/matrix.json /in/layout.json
#
# To run the web UI instead:
#   docker run -p 8000:8000 --entrypoint uvicorn klepcbgen webui:app \
#     --host 0.0.0.0 --port 8000
ENTRYPOINT ["python3", "/app/klepcbgen.py"]
