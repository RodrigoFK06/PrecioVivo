import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Precio Vivo — predicción de precios mayoristas con IA que le gana al baseline",
  description:
    "Inteligencia de precios del Gran Mercado Mayorista de Lima (GMML): precios diarios, qué subió y qué bajó, alertas y predicción con IA. Un modelo GBM ya le gana al baseline ingenuo en la mayoría de productos, validado walk-forward. Lo probamos en público — y decimos también qué no funciona.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col bg-slate-50 text-slate-900">
        <header className="sticky top-0 z-30 border-b border-slate-200/80 bg-white/85 backdrop-blur supports-[backdrop-filter]:bg-white/70">
          <div className="mx-auto max-w-6xl px-4 py-3 flex items-center justify-between gap-3">
            <Link href="/" className="group flex items-center gap-2.5 rounded-md">
              <span className="relative inline-flex h-6 w-6 items-center justify-center rounded-md bg-emerald-500 shadow-sm transition-transform group-hover:scale-105">
                <span className="absolute inline-block h-2 w-2 rounded-full bg-white/90" />
              </span>
              <span className="font-semibold tracking-tight text-lg leading-none">Precio Vivo</span>
              <span className="hidden sm:inline text-xs text-slate-400 font-medium border-l border-slate-200 pl-2.5">
                Gran Mercado Mayorista de Lima
              </span>
            </Link>
            <a
              href="#arkos"
              className="shrink-0 text-xs font-medium text-slate-500 hover:text-emerald-600 transition-colors"
            >
              hecho por <span className="font-semibold text-slate-700">Árkos</span>
            </a>
          </div>
        </header>

        <main className="flex-1">{children}</main>

        <footer id="arkos" className="border-t border-slate-200 bg-white mt-16">
          <div className="mx-auto max-w-6xl px-4 py-10 text-sm text-slate-500 space-y-5">
            <div className="relative overflow-hidden rounded-xl bg-slate-900 text-white p-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4 shadow-sm ring-1 ring-slate-900/5">
              <span
                aria-hidden
                className="pointer-events-none absolute -right-16 -top-16 h-44 w-44 rounded-full bg-emerald-500/15 blur-2xl"
              />
              <div className="relative">
                <p className="font-semibold text-white text-base">¿Necesitas software de datos + IA a medida?</p>
                <p className="text-slate-300 text-sm mt-1 max-w-md leading-relaxed">
                  Precio Vivo lo construyó <span className="font-semibold text-white">Árkos</span> — de PDFs públicos a un producto vivo, con pronóstico y consultas en lenguaje natural.
                </p>
              </div>
              <a
                href="mailto:hola@arkos.dev"
                className="relative shrink-0 rounded-md bg-emerald-500 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-emerald-400 hover:shadow-md transition-all text-center"
              >
                Hablemos →
              </a>
            </div>
            <p className="text-xs leading-relaxed">
              Fuente: <span className="font-medium">MIDAGRI – Gran Mercado Mayorista de Lima</span>, procesado por Precio Vivo.
              Republicamos únicamente hechos numéricos (precios, volúmenes); no redistribuimos los documentos fuente.
              <span className="text-slate-400"> Cifras referenciales, no oficiales.</span>
            </p>
          </div>
        </footer>
      </body>
    </html>
  );
}
