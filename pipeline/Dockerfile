# Pipeline + API de Precio Vivo.
#
# Base slim y no bookworm-full: pdfplumber y numpy traen ruedas binarias, así que
# no hace falta cadena de compilación. La imagen queda en cientos de MB en vez de
# más de 1 GB.
#
# NOTA: torch NO está en requirements a propósito (ver README, sección RAG). El
# embebedor local usa model2vec, que es numpy puro. Si alguna vez entra torch,
# esta imagen pasa de ~400 MB a ~3 GB — que sea una decisión consciente.
FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Las dependencias van en su propia capa: cambian mucho menos que el código, así
# que un cambio en un .py no reinstala numpy y scikit-learn.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Extras del transporte: API REST, servidor MCP y agente. Separados de
# requirements.txt porque el pipeline de datos NO los necesita para correr.
RUN pip install \
      "fastapi>=0.115" \
      "uvicorn[standard]>=0.30" \
      "mcp>=2.0" \
      "langgraph>=1.0" \
      "langchain-core>=1.0" \
      "langchain-openai>=1.0"

COPY pyproject.toml ./
COPY preciovivo/ ./preciovivo/
COPY schema.sql ./
RUN pip install --no-deps -e .

# Usuario sin privilegios: nada de esto necesita root, y un contenedor que corre
# como root es una escalada gratis si algo se escapa del proceso.
RUN useradd --create-home --uid 10001 preciovivo \
    && chown -R preciovivo:preciovivo /app
USER preciovivo

# El snapshot y el índice se montan como volumen (ver docker-compose.yml): son
# datos, no parte de la imagen. Hornearlos obligaría a reconstruir la imagen
# cada día hábil.
ENV PRECIOVIVO_EXPORT=/data/snapshot.json \
    PRECIOVIVO_DB=/data/preciovivo.db \
    PRECIOVIVO_API_HOST=0.0.0.0 \
    PRECIOVIVO_API_PORT=8000

EXPOSE 8000

# El healthcheck consulta /health, que reporta el estado REAL del dataset. Un
# contenedor "sano" sirviendo datos de hace dos semanas es peor que uno caído.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys,json; \
d=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=4)); \
sys.exit(0 if d.get('estado')=='ok' else 1)"

CMD ["python", "-m", "preciovivo.api"]
