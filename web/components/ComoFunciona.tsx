type Props = {
  mercado: string;
  nProductos: number;
  nFechas: number;
  iaGana: number;
  conForecast: number;
};

const PASOS = [
  {
    n: "01",
    titulo: "La fuente",
    texto:
      "El Gran Mercado Mayorista de Lima (MIDAGRI) publica cada día hábil un reporte oficial: el precio mayorista en soles por kilo y el volumen de ingreso en toneladas de cada producto. Es un PDF — no una API.",
  },
  {
    n: "02",
    titulo: "Cómo la nutrimos",
    texto:
      "Descargamos el PDF del día, lo leemos por coordenadas, normalizamos los nombres y lo acumulamos en una serie histórica continua. Sobre esa serie entrenamos un modelo (GBM) con validación walk-forward para predecir el precio de mañana.",
  },
  {
    n: "03",
    titulo: "El valor",
    texto:
      "De un PDF que casi nadie abre → datos limpios, vivos y consultables: el precio de hoy, qué se mueve, la tendencia y una predicción honesta — con su margen de error, y diciendo también qué no funciona.",
  },
];

/** Explica de dónde sale la data, cómo la procesamos y cuál es el valor. */
export default function ComoFunciona({ mercado, nProductos, nFechas, iaGana, conForecast }: Props) {
  const anios = Math.max(1, Math.round(nFechas / 250));
  return (
    <div>
      <ol className="grid gap-px overflow-hidden rounded-md border border-rule bg-rule sm:grid-cols-3">
        {PASOS.map((p) => (
          <li key={p.n} className="flex flex-col bg-card p-5">
            <span className="font-mono text-xs text-faint">{p.n}</span>
            <h3 className="mt-2 text-base font-semibold tracking-tight text-ink">{p.titulo}</h3>
            <p className="mt-1.5 text-sm leading-relaxed text-muted">{p.texto}</p>
          </li>
        ))}
      </ol>
      <p className="mt-3 text-xs leading-relaxed text-faint">
        <span className="text-muted">{mercado}</span> · {nProductos} productos · ~{anios} años de
        historia · actualizado cada día hábil · el modelo ya le gana al baseline en{" "}
        <span className="text-down tabular-nums">
          {iaGana} de {conForecast}
        </span>{" "}
        productos. Solo republicamos hechos numéricos; no redistribuimos los documentos fuente.
      </p>
    </div>
  );
}
