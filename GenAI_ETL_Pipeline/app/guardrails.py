"""Defense in depth: conservative signals, bounded tools, verified evidence references.

Pattern detection is a heuristic, not a proof that an input is safe.
"""
import re

INJECTION = re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|system)\s+instructions|(?:reveal|print|exfiltrate).{0,30}(?:api.?key|secret|system prompt)|<\|(?:system|assistant)\|>|bypass.{0,20}(?:approval|guardrail)|(?:disable|skip).{0,20}(?:human|approval|validation)",re.I|re.S)
SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}|-----BEGIN (?:RSA |EC )?PRIVATE KEY-----")


def inspect_text(text):
    return {"prompt_injection_signal": bool(INJECTION.search(text)), "secret_signal": bool(SECRET.search(text)), "heuristic": True}


def require_safe(text):
    findings=inspect_text(text)
    if findings["secret_signal"]:
        raise ValueError("Input contains a credential-like value. Remove secrets before indexing or sending to a model.")
    if findings["prompt_injection_signal"]:
        raise ValueError("Input contains a prompt-injection signal; review it before proceeding")
    return findings


def validate_claims(claims, hits):
    sources={hit["chunk_id"]:hit for hit in hits}
    validated=[]
    for claim in claims:
        if claim.chunk_id not in sources or not claim.quote.strip() or claim.quote not in sources[claim.chunk_id]["text"]:
            raise ValueError("Unverifiable citation or evidence quote; answer withheld")
        require_safe(claim.statement)
        validated.append({"statement":claim.statement,"chunk_id":claim.chunk_id,"quote":claim.quote,"title":sources[claim.chunk_id]["title"],"document_id":sources[claim.chunk_id]["document_id"],"start_offset":sources[claim.chunk_id]["start_offset"],"end_offset":sources[claim.chunk_id]["end_offset"]})
    return validated
