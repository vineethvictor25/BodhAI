import io
import re
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from .ai import provider, structured
from .models import ExtractedDocument
from .ingest import save_rows


def extract(raw: bytes, name: str):
    suffix = name.rsplit(".", 1)[-1].lower()
    image = None
    mime = "image/png"
    if suffix == "pdf":
        try:
            reader = PdfReader(io.BytesIO(raw))
        except (PdfReadError, ValueError):
            raise ValueError("Document is not a readable PDF") from None
        if reader.is_encrypted or len(reader.pages) > 30:
            raise ValueError("PDF must be unencrypted and at most 30 pages")
        text = "\n".join(f"[page {i + 1}] {p.extract_text() or ''}" for i, p in enumerate(reader.pages))
        if not text.strip() or not any(p.extract_text() for p in reader.pages):
            raise ValueError("Scanned PDF has no text layer. Export its pages as PNG/JPEG for live multimodal extraction.")
    elif suffix in ("png", "jpg", "jpeg"):
        valid = raw.startswith(b"\x89PNG\r\n\x1a\n") if suffix == "png" else raw.startswith(b"\xff\xd8\xff")
        if not valid:
            raise ValueError("File signature does not match its image extension")
        if provider() == "local":
            raise ValueError("Image extraction requires the live multimodal provider")
        image, text = raw, "Extract the product attributes from this image."
        mime = "image/png" if suffix == "png" else "image/jpeg"
    elif suffix in ("txt", "md"):
        text = raw.decode("utf-8-sig")
    else:
        raise ValueError("Extraction supports text PDF, TXT, MD, PNG and JPEG")
    if len(text) > 60000:
        raise ValueError("Document text exceeds 60,000 characters; split it into smaller documents")
    if provider() != "local":
        doc, metadata = structured("Extract product attributes, a short summary and sentiment. Return null for unsupported or absent facts. Do not infer prices or identifiers. Evidence must be short verbatim quotes; include page markers when available. Output is a proposal requiring human verification.", text, ExtractedDocument, image, mime)
    else:
        def attr(key):
            match = re.search(r"^\s*" + key + r"\s*[:=]\s*(.+)$", text, re.I | re.M)
            return match[1].strip() if match else None
        price = attr("price")
        doc = ExtractedDocument(product_id=attr("(?:product_id|sku)"), product_name=attr("(?:product_name|name)"), length=attr("length"), price=float(price) if price and re.fullmatch(r"\d+(?:\.\d+)?", price) else None, currency=attr("currency"), summary=" ".join(text.split())[:300], sentiment="negative" if re.search(r"\b(?:broken|poor|damaged|bad)\b", text, re.I) else "positive" if re.search(r"\b(?:excellent|great|good)\b", text, re.I) else "neutral", evidence=[line.strip()[:200] for line in text.splitlines() if ":" in line][:10])
        metadata = {"provider": "local", "mode": "label extraction and keyword sentiment; no LLM inference"}
    # Extraction is staged, never automatically published as trusted warehouse data.
    record = doc.model_dump(exclude={"summary", "sentiment", "evidence"})
    staged = save_rows([record], "extracted:" + name, raw, {**metadata, "kind": "document", "extraction": doc.model_dump(), "requires_review": True})
    return {"document": doc.model_dump(), "dataset": staged, "metadata": metadata, "requires_review": True}
