import { ImageResponse } from "next/og";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = "Precio Vivo — precios mayoristas del Gran Mercado de Lima, con IA";

// Tarjeta social editorial (monocromática) para compartir en LinkedIn / X.
export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background: "#f7f7f8",
          color: "#0c0c0d",
          padding: 72,
          fontFamily: "sans-serif",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            borderBottom: "3px solid #0c0c0d",
            paddingBottom: 20,
          }}
        >
          <div style={{ fontSize: 40, fontWeight: 700, letterSpacing: "-0.02em" }}>Precio Vivo</div>
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <div
              style={{
                width: 26,
                height: 26,
                border: "4px solid #52d2d7",
                transform: "rotate(15deg)",
              }}
            />
            <div
              style={{
                fontSize: 24,
                color: "#200e3a",
                textTransform: "uppercase",
                letterSpacing: "0.14em",
                fontWeight: 600,
              }}
            >
              por Árkos
            </div>
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column" }}>
          <div
            style={{
              fontSize: 78,
              fontWeight: 700,
              letterSpacing: "-0.03em",
              lineHeight: 1.04,
              maxWidth: 920,
            }}
          >
            Los precios del mercado, leídos con IA
          </div>
          <div style={{ fontSize: 30, color: "#52525b", maxWidth: 880, marginTop: 24, lineHeight: 1.3 }}>
            Precios diarios del Gran Mercado Mayorista de Lima — con predicción que le gana al
            baseline.
          </div>
        </div>

        <div style={{ display: "flex", gap: 18, fontSize: 24, color: "#52525b" }}>
          <span>2 años de historia</span>
          <span>·</span>
          <span>precios en S/ por kg</span>
          <span>·</span>
          <span style={{ color: "#16a34a", fontWeight: 600 }}>predicción con IA</span>
        </div>
      </div>
    ),
    { ...size },
  );
}
