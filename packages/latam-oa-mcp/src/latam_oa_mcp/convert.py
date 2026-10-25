"""PDF -> Markdown converter, run as a subprocess by the MCP server.

PyMuPDF prints parser messages to stdout, which would corrupt the MCP stdio
channel, so conversion never runs inside the server process.

Usage: python -m latam_oa_mcp.convert <input.pdf> <output.md> <max_pages> <mode>

Modes:
  layout  structured markdown (headings, tables), no OCR. Default and fastest for born-digital PDFs.
  plain   raw text per page; fallback when layout conversion is too slow.
  ocr     structured markdown with OCR (RapidOCR); for scanned PDFs without a text layer.
"""

import sys


def main() -> int:
    src, dst = sys.argv[1], sys.argv[2]
    max_pages = int(sys.argv[3]) if len(sys.argv) > 3 else 200
    mode = sys.argv[4] if len(sys.argv) > 4 else "layout"
    import pymupdf

    doc = pymupdf.open(src)
    pages = list(range(min(doc.page_count, max_pages)))
    if mode == "plain":
        md = "\n\n".join(f"<!-- page {i + 1} -->\n\n{doc[i].get_text().strip()}" for i in pages)
    else:
        import pymupdf4llm

        md = pymupdf4llm.to_markdown(doc, pages=pages, show_progress=False,
                                     use_ocr=(mode == "ocr"), force_ocr=(mode == "ocr"))
    with open(dst, "w", encoding="utf-8") as f:
        f.write(md)
        if doc.page_count > max_pages:
            f.write(f"\n\n> Conversion stopped at page {max_pages} of {doc.page_count}; "
                    "see the original PDF for the rest.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
