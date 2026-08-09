"""Índice vectorial de Precio Vivo: una interfaz, tres backends.

POR QUÉ TRES, Y POR QUÉ EL PRIMERO ES NUMPY
-------------------------------------------
Seamos honestos sobre la escala: el corpus tiene ~9.100 chunks. A 256 dimensiones
eso son 9 MB en RAM, y un k-NN exacto es UN producto matriz-vector — submilisegundo.
A este tamaño, la fuerza bruta de numpy no solo alcanza: es más rápida que
cualquier índice aproximado, porque no hay que construirlo ni recorrer su
estructura.

Entonces `NumpyIndex` no es un juguete ni un fallback. Es el ORÁCULO DE
REFERENCIA: k-NN exacto, sin aproximación, contra el que los tests verifican que
`SqliteVecIndex` y `PgVectorIndex` devuelven los mismos vecinos. Tener una
implementación obviamente correcta contra la cual validar las optimizadas es
mejor ingeniería que tener solo las optimizadas y esperar que estén bien.

Los otros dos existen por razones reales, no decorativas:
  SqliteVecIndex  Persiste junto a la BD que el proyecto ya usa; el índice
                  sobrevive al proceso y no hay que reconstruirlo al arrancar.
  PgVectorIndex   El destino de producción documentado, y el único de los tres
                  que hace ANN filtrado de verdad en el motor. Se activa con el
                  docker-compose del bloque 5.

COSENO SOBRE VECTORES NORMALIZADOS
----------------------------------
`embeddings.py` garantiza vectores L2-normalizados, así que el coseno es un
producto punto. sqlite-vec devuelve distancia L2; sobre vectores unitarios la
conversión es exacta: cos = 1 - d²/2. Se convierte en vez de configurar la
métrica del backend para no depender de qué opciones soporta cada versión — y
para que los tres backends devuelvan la MISMA escala y sean comparables contra
el oráculo.

FILTRO ANTES DEL k-NN
---------------------
El pre-filtro estructurado se aplica ANTES de buscar vecinos, no después. Filtrar
después obliga a adivinar cuánto sobre-pedir y puede devolver menos de k
resultados sin avisar.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .corpus import Chunk

# SQLite tiene un tope de parámetros por sentencia. Por debajo de esto el
# pre-filtro va como `rowid IN (...)`; por encima se sobre-pide y se filtra
# después (ver SqliteVecIndex.buscar).
LIMITE_PARAMS_IN = 900
# Factor de sobre-pedido cuando el filtro es demasiado amplio para `IN`.
SOBREPEDIDO = 8
# Candidatos extra que piden los backends con orden propio (sqlite-vec, pgvector)
# antes de reordenar de forma determinista. Ver `_ordenar_determinista`.
MARGEN_EMPATE = 16
# Granularidad de la clave de orden. Por encima del ruido de float32 (~1e-6 tras
# la conversión L2->coseno) y muy por debajo de cualquier diferencia de score con
# significado para la relevancia. Ver `_ordenar_determinista`.
EPS_ORDEN = 1e-5


# --------------------------------------------------------------------------- #
# Contrato
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class IndiceMeta:
    """Identidad del índice. Su razón de ser es `firma_embedder`.

    Un índice construido con un embebedor solo puede consultarse con el mismo:
    mezclarlos compara espacios vectoriales distintos y devuelve ruido con total
    confianza. `buscar` lo verifica y falla ruidosamente.

    `huella_corpus` detecta un índice viejo: si el corpus cambió y el índice no
    se reconstruyó, la huella no coincide.
    """
    firma_embedder: str
    dims: int
    granularidad: str
    n_chunks: int
    huella_corpus: str
    construido_en: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "IndiceMeta":
        return IndiceMeta(**{k: d[k] for k in IndiceMeta.__dataclass_fields__ if k in d})


@dataclass(frozen=True)
class Filtro:
    """Pre-filtro estructurado. Todos los campos son opcionales y se combinan con AND."""
    slugs: frozenset[str] | None = None
    tipos: frozenset[str] | None = None
    fecha_desde: str | None = None
    fecha_hasta: str | None = None

    def vacio(self) -> bool:
        return (self.slugs is None and self.tipos is None
                and self.fecha_desde is None and self.fecha_hasta is None)

    def acepta(self, c: Chunk) -> bool:
        """Evalúa el filtro sobre un chunk. Referencia semántica de los backends.

        Un chunk cubre un RANGO [fecha_inicio, fecha_fin]; el filtro de fechas es
        de SOLAPAMIENTO, no de contención. Una pregunta por el 5 de agosto debe
        traer la semana que contiene ese día, no descartarla por empezar el 3.
        """
        if self.slugs is not None and c.slug not in self.slugs:
            return False
        if self.tipos is not None and c.tipo not in self.tipos:
            return False
        if self.fecha_desde and c.fecha_fin and c.fecha_fin < self.fecha_desde:
            return False
        # noqa: SIM103 - la cadena de returns tempranos se lee mejor que una
        # sola expresión booleana de cuatro condiciones negadas.
        if self.fecha_hasta and c.fecha_inicio and c.fecha_inicio > self.fecha_hasta:  # noqa: SIM103
            return False
        return True


@dataclass(frozen=True)
class Resultado:
    chunk: Chunk
    score: float  # coseno en [-1, 1]; mayor es mejor


def huella_corpus(chunks: list[Chunk]) -> str:
    """sha256 estable del corpus: detecta índices desactualizados."""
    h = hashlib.sha256()
    for c in sorted(chunks, key=lambda x: x.id):
        h.update(c.id.encode("utf-8"))
        h.update(b"\0")
        h.update(c.texto.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()[:16]


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class IndiceVectorial(ABC):
    """Interfaz común. Los tres backends son intercambiables para `retrieval.py`."""

    meta: IndiceMeta | None = None

    @abstractmethod
    def construir(self, chunks: list[Chunk], vectores: np.ndarray,
                  granularidad: str, firma_embedder: str) -> None:
        """Puebla el índice. `vectores[i]` corresponde a `chunks[i]`."""

    @abstractmethod
    def buscar(self, vector: np.ndarray, k: int = 10,
               filtro: Filtro | None = None) -> list[Resultado]:
        """k vecinos más cercanos por coseno, respetando el pre-filtro."""

    def verificar_compatible(self, firma_embedder: str) -> None:
        """Falla ruidosamente si el embebedor no es el que construyó el índice."""
        if self.meta is None:
            raise RuntimeError("El índice está vacío: hay que construirlo o cargarlo.")
        if self.meta.firma_embedder != firma_embedder:
            raise RuntimeError(
                f"Embebedor incompatible con el índice. El índice se construyó con "
                f"'{self.meta.firma_embedder}' y se está consultando con "
                f"'{firma_embedder}'. Compararían espacios vectoriales distintos y "
                f"el resultado sería ruido. Reconstruye el índice o usa el mismo "
                f"embebedor.")

    def cerrar(self) -> None:  # noqa: B027 - default concreto, no abstracto
        """No-op por defecto: solo los backends con conexión lo sobrescriben."""


def _ordenar_determinista(resultados: list[Resultado], k: int) -> list[Resultado]:
    """Orden TOTAL e idéntico en los tres backends.

    Hay DOS fuentes de ambigüedad en el orden, y las dos hay que cerrarlas:

    1. EMPATES REALES. En este corpus abundan: las semanas de distintas
       variedades de papa generan texto casi idéntico y puntúan exactamente
       igual. Cada backend desempata a su manera (el orden interno de sqlite-vec
       no es el argsort de numpy).

    2. RUIDO NUMÉRICO. Los backends calculan el MISMO coseno por caminos
       distintos: numpy hace el producto punto directo; sqlite-vec devuelve
       distancia L2 y aquí se convierte con 1 - d²/2, que amplifica el redondeo
       de float32. Resultado observado: 0.235702 contra 0.235703 para el mismo
       par. No es un empate —los números difieren— pero la diferencia carece de
       significado y basta para voltear el orden.

    Por eso la clave de orden se cuantiza a EPS_ORDEN antes de desempatar por id:
    así el ruido numérico se convierte en empate explícito y el id —único y
    estable— lo resuelve igual en todos lados. El score que se DEVUELVE conserva
    su precisión completa; solo se cuantiza para ordenar.

    QUÉ GARANTIZA Y QUÉ NO. Garantiza reproducibilidad DENTRO de un backend: la
    misma consulta sobre el mismo índice devuelve siempre el mismo orden, que es
    lo que las evaluaciones necesitan para que recall@k no oscile entre corridas.
    NO garantiza identidad exacta de ids ENTRE backends: cuantizar crea bordes de
    redondeo, y dos scores que difieren en 2e-7 pueden caer a lados distintos de
    uno. Eso es inherente a calcular lo mismo por dos caminos en float32, no un
    defecto que se pueda pulir. La propiedad de corrección que sí se exige —y que
    los tests verifican— es que los backends devuelvan resultados IGUAL DE
    RELEVANTES: mismos scores posición a posición, y cualquier id distinto,
    empatado en score con el que ocupa su lugar.
    """
    resultados.sort(key=lambda r: (-round(r.score / EPS_ORDEN), r.chunk.id))
    return resultados[:k]


def _validar(chunks: list[Chunk], vectores: np.ndarray) -> np.ndarray:
    v = np.asarray(vectores, dtype=np.float32)
    if v.ndim != 2:
        raise ValueError(f"vectores debe ser 2D, es {v.ndim}D")
    if len(chunks) != v.shape[0]:
        raise ValueError(
            f"desajuste: {len(chunks)} chunks contra {v.shape[0]} vectores")
    return v


# --------------------------------------------------------------------------- #
# 1) NumpyIndex — el oráculo exacto
# --------------------------------------------------------------------------- #
class NumpyIndex(IndiceVectorial):
    """k-NN exacto en memoria. Referencia de corrección y backend del sitio.

    También es el que se serializa al artefacto estático que viaja en el deploy:
    cuantizado a int8 pesa ~9.100 x 256 = 2,3 MB.
    """

    def __init__(self) -> None:
        self.chunks: list[Chunk] = []
        self.vectores: np.ndarray = np.zeros((0, 0), dtype=np.float32)
        self.meta = None

    def construir(self, chunks: list[Chunk], vectores: np.ndarray,
                  granularidad: str, firma_embedder: str) -> None:
        v = _validar(chunks, vectores)
        self.chunks = list(chunks)
        self.vectores = v
        self.meta = IndiceMeta(
            firma_embedder=firma_embedder, dims=int(v.shape[1]),
            granularidad=granularidad, n_chunks=len(chunks),
            huella_corpus=huella_corpus(chunks), construido_en=_ahora())

    def buscar(self, vector: np.ndarray, k: int = 10,
               filtro: Filtro | None = None) -> list[Resultado]:
        if not self.chunks:
            return []
        q = np.asarray(vector, dtype=np.float32).ravel()
        if q.shape[0] != self.vectores.shape[1]:
            raise ValueError(
                f"la consulta tiene {q.shape[0]} dims y el índice {self.vectores.shape[1]}")

        if filtro is not None and not filtro.vacio():
            idx = np.array([i for i, c in enumerate(self.chunks) if filtro.acepta(c)],
                           dtype=np.int64)
            if idx.size == 0:
                return []
        else:
            idx = np.arange(len(self.chunks), dtype=np.int64)

        scores = self.vectores[idx] @ q
        k_efectivo = min(k, idx.size)
        # argpartition + orden solo del top-k: O(n) en vez de O(n log n). Se toma
        # un margen extra para que los empates que caen justo en el corte se
        # resuelvan con el mismo criterio que los demás backends.
        n_cand = min(idx.size, k_efectivo + MARGEN_EMPATE)
        if n_cand < idx.size:
            top = np.argpartition(-scores, n_cand - 1)[:n_cand]
        else:
            top = np.arange(idx.size)
        return _ordenar_determinista(
            [Resultado(chunk=self.chunks[int(idx[t])], score=float(scores[t]))
             for t in top], k_efectivo)

    # --- persistencia ----------------------------------------------------
    def guardar(self, ruta: str | Path) -> None:
        """Guarda como .npz (vectores float32) + .json (chunks y meta)."""
        ruta = Path(ruta)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(ruta.with_suffix(".npz"), vectores=self.vectores)
        ruta.with_suffix(".json").write_text(json.dumps({
            "meta": self.meta.to_dict() if self.meta else None,
            "chunks": [c.to_dict() for c in self.chunks],
        }, ensure_ascii=False), encoding="utf-8")

    def cargar(self, ruta: str | Path) -> "NumpyIndex":
        ruta = Path(ruta)
        datos = json.loads(ruta.with_suffix(".json").read_text(encoding="utf-8"))
        self.chunks = [Chunk(**c) for c in datos["chunks"]]
        self.vectores = np.load(ruta.with_suffix(".npz"))["vectores"].astype(np.float32)
        self.meta = IndiceMeta.from_dict(datos["meta"]) if datos.get("meta") else None
        return self


# --------------------------------------------------------------------------- #
# 2) SqliteVecIndex — persistente, junto a la BD del proyecto
# --------------------------------------------------------------------------- #
_DDL = """
CREATE TABLE IF NOT EXISTS rag_chunks (
    rowid_ref     INTEGER PRIMARY KEY,
    id            TEXT UNIQUE NOT NULL,
    tipo          TEXT NOT NULL,
    texto         TEXT NOT NULL,
    slug          TEXT,
    producto      TEXT,
    mercado       TEXT,
    fecha_inicio  TEXT,
    fecha_fin     TEXT
);
CREATE INDEX IF NOT EXISTS ix_rag_slug  ON rag_chunks (slug);
CREATE INDEX IF NOT EXISTS ix_rag_tipo  ON rag_chunks (tipo);
CREATE INDEX IF NOT EXISTS ix_rag_fecha ON rag_chunks (fecha_inicio, fecha_fin);
CREATE TABLE IF NOT EXISTS rag_meta (clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
"""


class SqliteVecIndex(IndiceVectorial):
    """Índice persistente sobre SQLite + extensión sqlite-vec.

    El pre-filtro va como `rowid IN (...)`, que sqlite-vec sí soporta junto a
    MATCH. Cuando el conjunto permitido supera el tope de parámetros de SQLite,
    se sobre-pide y se filtra en Python — ver `buscar`.
    """

    def __init__(self, ruta: str | Path = ":memory:"):
        self.ruta = str(ruta)
        self._conn = sqlite3.connect(self.ruta)
        self._conn.row_factory = sqlite3.Row
        try:
            import sqlite_vec
        except ImportError as e:  # noqa: TRY003
            raise RuntimeError(
                "SqliteVecIndex necesita la extensión: pip install sqlite-vec. "
                "Alternativa sin dependencias: NumpyIndex.") from e
        try:
            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
        except AttributeError as e:  # build de Python sin carga de extensiones
            raise RuntimeError(
                "Este Python no permite cargar extensiones de SQLite, así que "
                "sqlite-vec no puede usarse. Usa NumpyIndex (k-NN exacto, "
                "suficiente a esta escala).") from e
        self._sqlite_vec = sqlite_vec
        self._conn.executescript(_DDL)
        self.meta = self._leer_meta()

    # --- meta ------------------------------------------------------------
    def _leer_meta(self) -> IndiceMeta | None:
        try:
            filas = dict(self._conn.execute(
                "SELECT clave, valor FROM rag_meta").fetchall())
        except sqlite3.Error:
            return None
        if not filas:
            return None
        return IndiceMeta(
            firma_embedder=filas.get("firma_embedder", ""),
            dims=int(filas.get("dims", 0)),
            granularidad=filas.get("granularidad", ""),
            n_chunks=int(filas.get("n_chunks", 0)),
            huella_corpus=filas.get("huella_corpus", ""),
            construido_en=filas.get("construido_en", ""))

    def construir(self, chunks: list[Chunk], vectores: np.ndarray,
                  granularidad: str, firma_embedder: str) -> None:
        v = _validar(chunks, vectores)
        dims = int(v.shape[1])
        cur = self._conn.cursor()
        cur.execute("DROP TABLE IF EXISTS rag_vec")
        cur.execute(f"CREATE VIRTUAL TABLE rag_vec USING vec0(embedding float[{dims}])")
        cur.execute("DELETE FROM rag_chunks")
        # strict=True: _validar ya garantiza longitudes iguales, así que un
        # desajuste acá sería un bug silencioso que emparejaría el vector
        # equivocado con cada chunk.
        for i, (c, vec) in enumerate(zip(chunks, v, strict=True), start=1):
            cur.execute(
                "INSERT INTO rag_chunks (rowid_ref, id, tipo, texto, slug, producto, "
                "mercado, fecha_inicio, fecha_fin) VALUES (?,?,?,?,?,?,?,?,?)",
                (i, c.id, c.tipo, c.texto, c.slug, c.producto, c.mercado,
                 c.fecha_inicio, c.fecha_fin))
            cur.execute("INSERT INTO rag_vec (rowid, embedding) VALUES (?, ?)",
                        (i, self._sqlite_vec.serialize_float32(vec.tolist())))
        meta = IndiceMeta(
            firma_embedder=firma_embedder, dims=dims, granularidad=granularidad,
            n_chunks=len(chunks), huella_corpus=huella_corpus(chunks),
            construido_en=_ahora())
        cur.execute("DELETE FROM rag_meta")
        cur.executemany("INSERT INTO rag_meta (clave, valor) VALUES (?,?)",
                        [(k, str(val)) for k, val in meta.to_dict().items()])
        self._conn.commit()
        self.meta = meta

    def _rowids_permitidos(self, filtro: Filtro) -> list[int]:
        cond, args = [], []
        if filtro.slugs is not None:
            if not filtro.slugs:
                return []
            marcas = ",".join("?" * len(filtro.slugs))
            cond.append(f"slug IN ({marcas})")
            args.extend(sorted(filtro.slugs))
        if filtro.tipos is not None:
            if not filtro.tipos:
                return []
            marcas = ",".join("?" * len(filtro.tipos))
            cond.append(f"tipo IN ({marcas})")
            args.extend(sorted(filtro.tipos))
        # Solapamiento de rangos, igual que Filtro.acepta.
        if filtro.fecha_desde:
            cond.append("(fecha_fin = '' OR fecha_fin >= ?)")
            args.append(filtro.fecha_desde)
        if filtro.fecha_hasta:
            cond.append("(fecha_inicio = '' OR fecha_inicio <= ?)")
            args.append(filtro.fecha_hasta)
        sql = "SELECT rowid_ref FROM rag_chunks"
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        return [r[0] for r in self._conn.execute(sql, args).fetchall()]

    def _chunk(self, fila) -> Chunk:
        return Chunk(id=fila["id"], tipo=fila["tipo"], texto=fila["texto"],
                     slug=fila["slug"], producto=fila["producto"],
                     mercado=fila["mercado"], fecha_inicio=fila["fecha_inicio"],
                     fecha_fin=fila["fecha_fin"])

    def buscar(self, vector: np.ndarray, k: int = 10,
               filtro: Filtro | None = None) -> list[Resultado]:
        if self.meta is None or self.meta.n_chunks == 0:
            return []
        q = self._sqlite_vec.serialize_float32(
            np.asarray(vector, dtype=np.float32).ravel().tolist())

        permitidos: list[int] | None = None
        if filtro is not None and not filtro.vacio():
            permitidos = self._rowids_permitidos(filtro)
            if not permitidos:
                return []

        # Se piden k + margen para que el reordenamiento determinista tenga con
        # qué resolver los empates que caen justo en el corte.
        pedir_k = min(k + MARGEN_EMPATE, self.meta.n_chunks)

        if permitidos is not None and len(permitidos) <= LIMITE_PARAMS_IN:
            marcas = ",".join("?" * len(permitidos))
            sql = (f"SELECT rowid, distance FROM rag_vec WHERE embedding MATCH ? "
                   f"AND k = ? AND rowid IN ({marcas})")
            filas = self._conn.execute(
                sql, (q, min(pedir_k, len(permitidos)), *permitidos)).fetchall()
        else:
            # El filtro permite demasiadas filas para `IN` (tope de parámetros de
            # SQLite). Se sobre-pide y se descarta en Python: como el conjunto
            # permitido es grande, el top-k sin filtrar ya es casi todo válido y
            # converge en una pasada.
            permitido_set = set(permitidos) if permitidos is not None else None
            pedir = (pedir_k if permitido_set is None
                     else min(pedir_k * SOBREPEDIDO, self.meta.n_chunks))
            crudo = self._conn.execute(
                "SELECT rowid, distance FROM rag_vec WHERE embedding MATCH ? AND k = ?",
                (q, pedir)).fetchall()
            filas = [f for f in crudo
                     if permitido_set is None or f["rowid"] in permitido_set][:pedir_k]

        out: list[Resultado] = []
        for f in filas:
            fila = self._conn.execute(
                "SELECT * FROM rag_chunks WHERE rowid_ref = ?", (f["rowid"],)).fetchone()
            if fila is None:
                continue
            # Vectores unitarios: cos = 1 - d²/2. Exacto, no aproximado.
            d = float(f["distance"])
            out.append(Resultado(chunk=self._chunk(fila), score=1.0 - (d * d) / 2.0))
        return _ordenar_determinista(out, k)

    def cerrar(self) -> None:
        self._conn.close()


# --------------------------------------------------------------------------- #
# 3) PgVectorIndex — el destino de producción
# --------------------------------------------------------------------------- #
class PgVectorIndex(IndiceVectorial):
    """Índice sobre PostgreSQL + pgvector.

    ESTADO: escrito y conforme a la interfaz, pero TODAVÍA NO EJECUTADO contra un
    servidor real — en esta máquina no hay Postgres (el proyecto corre sobre
    SQLite; ver README). Su test está marcado `skipif` y se activa en cuanto
    DATABASE_URL apunte a un Postgres con la extensión, que es lo que trae el
    docker-compose del bloque 5. Hasta entonces, no afirmamos que funcione.

    Es el único de los tres backends que hace ANN filtrado nativamente: el WHERE
    y el orden por distancia los resuelve el motor en una sola consulta.
    """

    def __init__(self, dsn: str | None = None, tabla: str = "rag_chunks"):
        self.dsn = dsn or os.environ.get("DATABASE_URL")
        if not self.dsn:
            raise RuntimeError(
                "PgVectorIndex necesita DATABASE_URL (o dsn=). Sin Postgres, usa "
                "SqliteVecIndex o NumpyIndex.")
        try:
            import psycopg
        except ImportError as e:  # noqa: TRY003
            raise RuntimeError("PgVectorIndex necesita: pip install psycopg") from e
        self._psycopg = psycopg
        self.tabla = tabla
        self._conn = psycopg.connect(self.dsn)
        self.meta = self._leer_meta()

    def _leer_meta(self) -> IndiceMeta | None:
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT clave, valor FROM rag_meta")
                filas = dict(cur.fetchall())
        except Exception:  # noqa: BLE001 - tabla ausente = índice sin construir
            self._conn.rollback()
            return None
        if not filas:
            return None
        return IndiceMeta(
            firma_embedder=filas.get("firma_embedder", ""),
            dims=int(filas.get("dims", 0)),
            granularidad=filas.get("granularidad", ""),
            n_chunks=int(filas.get("n_chunks", 0)),
            huella_corpus=filas.get("huella_corpus", ""),
            construido_en=filas.get("construido_en", ""))

    def construir(self, chunks: list[Chunk], vectores: np.ndarray,
                  granularidad: str, firma_embedder: str) -> None:
        v = _validar(chunks, vectores)
        dims = int(v.shape[1])
        with self._conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(f"DROP TABLE IF EXISTS {self.tabla}")
            cur.execute(f"""
                CREATE TABLE {self.tabla} (
                    id            TEXT PRIMARY KEY,
                    tipo          TEXT NOT NULL,
                    texto         TEXT NOT NULL,
                    slug          TEXT,
                    producto      TEXT,
                    mercado       TEXT,
                    fecha_inicio  DATE,
                    fecha_fin     DATE,
                    embedding     vector({dims}) NOT NULL
                )""")
            cur.executemany(
                f"INSERT INTO {self.tabla} (id, tipo, texto, slug, producto, mercado, "
                f"fecha_inicio, fecha_fin, embedding) "
                f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [(c.id, c.tipo, c.texto, c.slug, c.producto, c.mercado,
                  c.fecha_inicio or None, c.fecha_fin or None, str(vec.tolist()))
                 for c, vec in zip(chunks, v, strict=True)])
            cur.execute(f"CREATE INDEX ON {self.tabla} USING hnsw "
                        f"(embedding vector_cosine_ops)")
            cur.execute(f"CREATE INDEX ON {self.tabla} (slug)")
            cur.execute(f"CREATE INDEX ON {self.tabla} (tipo)")
            cur.execute("CREATE TABLE IF NOT EXISTS rag_meta ("
                        "clave TEXT PRIMARY KEY, valor TEXT NOT NULL)")
            cur.execute("DELETE FROM rag_meta")
            meta = IndiceMeta(
                firma_embedder=firma_embedder, dims=dims, granularidad=granularidad,
                n_chunks=len(chunks), huella_corpus=huella_corpus(chunks),
                construido_en=_ahora())
            cur.executemany("INSERT INTO rag_meta (clave, valor) VALUES (%s,%s)",
                            [(k, str(val)) for k, val in meta.to_dict().items()])
        self._conn.commit()
        self.meta = meta

    def buscar(self, vector: np.ndarray, k: int = 10,
               filtro: Filtro | None = None) -> list[Resultado]:
        if self.meta is None or self.meta.n_chunks == 0:
            return []
        q = str(np.asarray(vector, dtype=np.float32).ravel().tolist())
        cond, args = [], []
        if filtro is not None and not filtro.vacio():
            if filtro.slugs is not None:
                if not filtro.slugs:
                    return []
                cond.append("slug = ANY(%s)")
                args.append(sorted(filtro.slugs))
            if filtro.tipos is not None:
                if not filtro.tipos:
                    return []
                cond.append("tipo = ANY(%s)")
                args.append(sorted(filtro.tipos))
            if filtro.fecha_desde:
                cond.append("(fecha_fin IS NULL OR fecha_fin >= %s)")
                args.append(filtro.fecha_desde)
            if filtro.fecha_hasta:
                cond.append("(fecha_inicio IS NULL OR fecha_inicio <= %s)")
                args.append(filtro.fecha_hasta)
        where = (" WHERE " + " AND ".join(cond)) if cond else ""
        # `<=>` es distancia coseno en pgvector: similitud = 1 - distancia.
        sql = (f"SELECT id, tipo, texto, slug, producto, mercado, fecha_inicio, "
               f"fecha_fin, 1 - (embedding <=> %s::vector) AS score "
               f"FROM {self.tabla}{where} ORDER BY embedding <=> %s::vector LIMIT %s")
        with self._conn.cursor() as cur:
            # k + margen, por la misma razón que en sqlite: el desempate
            # determinista necesita ver los candidatos del borde.
            cur.execute(sql, (q, *args, q, k + MARGEN_EMPATE))
            filas = cur.fetchall()
        return _ordenar_determinista([Resultado(
            chunk=Chunk(id=f[0], tipo=f[1], texto=f[2], slug=f[3], producto=f[4],
                        mercado=f[5],
                        fecha_inicio=f[6].isoformat() if f[6] else "",
                        fecha_fin=f[7].isoformat() if f[7] else ""),
            score=float(f[8])) for f in filas], k)

    def cerrar(self) -> None:
        self._conn.close()


# --------------------------------------------------------------------------- #
# Selección
# --------------------------------------------------------------------------- #
def get_indice(backend: str | None = None, ruta: str | Path | None = None
               ) -> IndiceVectorial:
    """Devuelve el backend pedido (o PRECIOVIVO_INDEX_BACKEND; default numpy).

    El default es `numpy` porque a ~9.100 chunks es exacto, instantáneo y sin
    dependencias. `sqlite` persiste; `pgvector` es el destino de producción.
    """
    backend = (backend or os.environ.get("PRECIOVIVO_INDEX_BACKEND", "numpy")).lower()
    if backend == "numpy":
        return NumpyIndex()
    if backend in ("sqlite", "sqlite-vec", "sqlitevec"):
        return SqliteVecIndex(ruta or os.environ.get(
            "PRECIOVIVO_INDEX_PATH", "../data/preciovivo_rag.db"))
    if backend in ("pgvector", "postgres", "pg"):
        return PgVectorIndex()
    raise ValueError(f"backend de índice desconocido: {backend!r}")
