-- Precio Vivo — canonical schema (PostgreSQL / Supabase).
-- The loader (store.py) generates a SQLite-adapted variant for local dev, but
-- THIS file is the source of truth for the production Supabase database.

CREATE TABLE IF NOT EXISTS mercados (
    id       SERIAL PRIMARY KEY,
    codigo   TEXT UNIQUE NOT NULL,        -- GMML, MMF2, MMP, AVES
    nombre   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS productos (
    id              SERIAL PRIMARY KEY,
    nombre_canonico TEXT UNIQUE NOT NULL,
    categoria       TEXT,
    unidad_default  TEXT,
    equiv_kg        REAL
);

CREATE TABLE IF NOT EXISTS precios_diarios (
    fecha            DATE    NOT NULL,
    mercado_id       INTEGER NOT NULL REFERENCES mercados(id),
    producto_id      INTEGER NOT NULL REFERENCES productos(id),
    -- masa de ingreso (toneladas) = la señal de oferta
    masa_ayer        REAL, masa_hoy REAL, masa_7d REAL, masa_4lun REAL,
    unidad           TEXT,
    equiv_kg         REAL,
    -- precio por unidad de medida (tal como lo publica el reporte)
    precio_ayer_unit REAL, precio_hoy_unit REAL, precio_7d_unit REAL,
    -- precio normalizado a S/ por kg (computado)
    precio_ayer_kg   REAL, precio_hoy_kg REAL, precio_7d_kg REAL,
    tendencia        TEXT,
    fuente           TEXT,                 -- 'reporte-335'
    cargado_en       TIMESTAMP NOT NULL,
    PRIMARY KEY (producto_id, mercado_id, fecha)
);

CREATE INDEX IF NOT EXISTS ix_precios_prod_fecha ON precios_diarios (producto_id, fecha);
CREATE INDEX IF NOT EXISTS ix_precios_fecha      ON precios_diarios (fecha);

-- Pronósticos honestos (Fase 3): un registro por producto/mercado/origen/horizonte.
-- precio_estimado, intervalo y mae provienen de forecast.py (baselines simples,
-- validación walk-forward anti-leakage). NO son cifras del reporte: son estimaciones.
CREATE TABLE IF NOT EXISTS pronosticos (
    producto_id     INTEGER NOT NULL REFERENCES productos(id),
    mercado_id      INTEGER NOT NULL REFERENCES mercados(id),
    fecha_generado  DATE    NOT NULL,
    horizonte       INTEGER NOT NULL,        -- horizonte en fechas hábiles
    precio_estimado REAL,
    metodo          TEXT,                     -- seasonal_naive | media_tendencia | naive | sin_datos
    intervalo_lo    REAL,
    intervalo_hi    REAL,
    mae             REAL,
    PRIMARY KEY (producto_id, mercado_id, fecha_generado, horizonte)
);

CREATE INDEX IF NOT EXISTS ix_pronosticos_prod ON pronosticos (producto_id, fecha_generado);

-- Provenance / legal trail: one row per harvested source file (§0.5/§4).
CREATE TABLE IF NOT EXISTS fuentes_raw (
    id          SERIAL PRIMARY KEY,
    fuente      TEXT NOT NULL,
    fecha       DATE,
    url         TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    bytes       INTEGER,
    descargado_en TIMESTAMP NOT NULL,
    UNIQUE (fuente, sha256)
);
