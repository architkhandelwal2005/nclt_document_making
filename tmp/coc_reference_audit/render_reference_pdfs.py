from pathlib import Path

import pypdfium2 as pdfium


ROOT = Path(r"E:\nclt document\tmp\coc_reference_audit")


def main() -> None:
    pdf_paths = list(ROOT.glob("*/reference.pdf")) + list((ROOT / "generated").glob("*.pdf"))
    for pdf_path in sorted(pdf_paths):
        render_dir = pdf_path.parent / ("render" if pdf_path.name == "reference.pdf" else f"{pdf_path.stem}-render")
        render_dir.mkdir(exist_ok=True)
        pdf = pdfium.PdfDocument(pdf_path)
        for index in range(len(pdf)):
            page = pdf[index]
            bitmap = page.render(scale=2.0)
            image = bitmap.to_pil()
            image.save(render_dir / f"page-{index + 1}.png")
            page.close()
        pdf.close()
        print(f"{pdf_path.parent.name}: {len(list(render_dir.glob('page-*.png')))} pages")


if __name__ == "__main__":
    main()
