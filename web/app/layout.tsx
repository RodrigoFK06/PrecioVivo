import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Precio Vivo — precios mayoristas del Perú, en vivo",
  description:
    "Precios diarios del Gran Mercado Mayorista de Lima (GMML): qué subió, qué bajó y cómo viene la tendencia. Inteligencia de mercado con IA.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="es" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col bg-slate-50 text-slate-900">
        <header className="border-b border-slate-200 bg-white">
          <div className="mx-auto max-w-6xl px-4 py-3 flex items-center justify-between">
            <Link href="/" className="flex items-center gap-2">
              <span className="inline-block h-5 w-5 rounded-sm bg-emerald-500" />
              <span className="font-semibold tracking-tight text-lg">Precio Vivo</span>
              <span className="hidden sm:inline text-xs text-slate-400 font-medium">
                Gran Mercado Mayorista de Lima
              </span>
            </Link>
            <a
              href="#arkos"
              className="text-xs font-medium text-slate-500 hover:text-emerald-600 transition-colors"
            >
              hecho por <span className="font-semibold">Árkos</span>
            </a>
          </div>
        </header>

        <main className="flex-1">{children}</main>

        <footer id="arkos" className="border-t border-slate-200 bg-white mt-12">
          <div className="mx-auto max-w-6xl px-4 py-8 text-sm text-slate-500 space-y-4">
            <div className="rounded-lg bg-slate-900 text-white p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div>
                <p className="font-semibold text-white">¿Necesitas software de datos + IA a medida?</p>
                <p className="text-slate-300 text-xs mt-0.5">
                  Precio Vivo lo construyó <span className="font-semibold text-white">Árkos</span> — de PDFs públicos a un producto vivo.
                </p>
              </div>
              <a
                href="mailto:hola@arkos.dev"
                className="shrink-0 rounded-md bg-emerald-500 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-400 transition-colors text-center"
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
