import type { Metadata } from "next";
import { Geist, Geist_Mono, Fraunces } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
  style: ["normal", "italic"],
});

export const metadata: Metadata = {
  title: "Precio Vivo — predicción de precios mayoristas con IA que le gana al baseline",
  description:
    "Inteligencia de precios del Gran Mercado Mayorista de Lima (GMML): precios diarios, qué subió y qué bajó, alertas y predicción con IA. Un modelo GBM ya le gana al baseline ingenuo en la mayoría de productos, validado walk-forward. Lo probamos en público — y decimos también qué no funciona.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="es"
      className={`${geistSans.variable} ${geistMono.variable} ${fraunces.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        {/* ── Masthead ─────────────────────────────────────────────── */}
        <header className="sticky top-0 z-30 border-b-2 border-ink/85 bg-paper/90 backdrop-blur supports-[backdrop-filter]:bg-paper/75">
          <div className="mx-auto max-w-6xl px-4 py-2.5 flex items-baseline justify-between gap-3">
            <Link href="/" className="group inline-flex items-baseline gap-2.5">
              <span className="font-serif text-2xl font-semibold text-ink group-hover:text-accent transition-colors">
                Precio Vivo
              </span>
              <span className="hidden sm:inline eyebrow border-l border-rule pl-2.5">
                Gran Mercado Mayorista de Lima
              </span>
            </Link>
            <a
              href="#arkos"
              className="shrink-0 text-xs text-muted hover:text-accent transition-colors"
            >
              hecho por <span className="font-serif font-semibold text-ink">Árkos</span>
            </a>
          </div>
        </header>

        <main className="flex-1">{children}</main>

        {/* ── Colofón + CTA Árkos ──────────────────────────────────── */}
        <footer id="arkos" className="mt-20 border-t-2 border-ink/85 bg-paper">
          <div className="mx-auto max-w-6xl px-4 py-10">
            <div className="flex flex-col gap-5 border-b border-rule pb-8 sm:flex-row sm:items-end sm:justify-between">
              <div className="max-w-md">
                <p className="eyebrow">Hecho por Árkos</p>
                <p className="mt-2 font-serif text-2xl leading-snug text-ink">
                  ¿Necesitas software de datos + IA a medida?
                </p>
                <p className="mt-2 text-sm text-muted leading-relaxed">
                  Precio Vivo lo construyó Árkos — de PDFs públicos del Estado a un producto vivo,
                  con predicción y consultas en lenguaje natural.
                </p>
              </div>
              <a
                href="mailto:hola@arkos.dev"
                className="shrink-0 inline-flex items-center gap-1.5 border border-ink bg-ink px-5 py-2.5 text-sm font-medium text-paper hover:bg-accent hover:border-accent transition-colors"
              >
                Hablemos →
              </a>
            </div>
            <p className="mt-6 text-xs leading-relaxed text-faint">
              Fuente:{" "}
              <span className="text-muted">MIDAGRI – Gran Mercado Mayorista de Lima</span>, procesado
              por Precio Vivo. Republicamos únicamente hechos numéricos (precios, volúmenes); no
              redistribuimos los documentos fuente. Cifras referenciales, no oficiales.
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
