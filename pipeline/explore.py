"""Throwaway geometry probe for the GMML reporte-335 PDF.
Dumps word x/y positions so we can see the real column bands and row clustering
that `pdftotext -layout` scrambles. Run: python explore.py <pdf>
"""
import sys
import pdfplumber

pdf_path = sys.argv[1] if len(sys.argv) > 1 else "../data/samples/reporte_335_2026-06-22.pdf"

with pdfplumber.open(pdf_path) as pdf:
    print(f"pages={len(pdf.pages)}")
    page = pdf.pages[0]
    print(f"page0 size: width={page.width:.0f} height={page.height:.0f}")
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    print(f"word count p0={len(words)}")

    # Cluster words into rows by their 'top' coordinate (tolerance ~3px)
    rows = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        placed = False
        for r in rows:
            if abs(r["top"] - w["top"]) <= 3:
                r["words"].append(w)
                placed = True
                break
        if not placed:
            rows.append({"top": w["top"], "words": [w]})

    print(f"\nrow count p0={len(rows)}")
    print("\n=== first 40 rows: top | x0-positions | text ===")
    for r in rows[:40]:
        ws = sorted(r["words"], key=lambda w: w["x0"])
        xs = " ".join(f"{w['x0']:.0f}" for w in ws)
        txt = " | ".join(w["text"] for w in ws)
        print(f"y={r['top']:6.1f}  x[{xs}]")
        print(f"          {txt}")

    # Histogram of x0 starts to reveal column bands
    print("\n=== x0 histogram (column left-edges) ===")
    from collections import Counter
    c = Counter(round(w["x0"] / 5) * 5 for w in words)
    for x in sorted(c):
        print(f"  x~{x:4d}: {'#' * c[x]} ({c[x]})")
