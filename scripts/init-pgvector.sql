-- Extensión pgvector al inicializar la base.
--
-- Va con prefijo 10- para que corra ANTES que 20-schema.sql: si el esquema
-- alguna vez declara una columna `vector`, la extensión ya tiene que existir.
--
-- La imagen pgvector/pgvector trae la extensión compilada; esto solo la habilita
-- en esta base. Postgres solo ejecuta docker-entrypoint-initdb.d cuando el
-- volumen está vacío: si cambias esto, hay que borrar el volumen (`docker
-- compose down -v`) para que se vuelva a aplicar.
CREATE EXTENSION IF NOT EXISTS vector;
