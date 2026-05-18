# aegis_solana/rugcheck_client.py
"""
RugCheck.xyz API client for Solana token risk analysis.
Free tier: public access, no API key required.
"""
import httpx
from typing import Dict, Any


class RugCheckClient:
    def __init__(self, debug: bool = False):
        self.base_url = "https://api.rugcheck.xyz"
        self.debug = debug

    async def get_summary(self, mint: str) -> Dict[str, Any]:
        """Fetch a quick risk summary for a token mint."""
        url = f"{self.base_url}/v1/tokens/{mint}/report"
        if self.debug:
            print(f"[DEBUG] RugCheck summary request: {url}")

        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(url)
                if self.debug:
                    print(f"[DEBUG] RugCheck response status: {resp.status_code}")

                if resp.status_code == 404:
                    return {"error": "Token not found", "score": 0, "risks": []}
                elif resp.status_code == 429:
                    return {"error": "Rate limit exceeded", "score": 0, "risks": []}

                resp.raise_for_status()
                data = resp.json()
                return self._parse_summary(data)

        except httpx.TimeoutException:
            return {"error": "Timeout", "score": 0, "risks": []}
        except Exception as e:
            if self.debug:
                print(f"[DEBUG] RugCheck exception: {type(e).__name__}: {e}")
            return {"error": str(e), "score": 0, "risks": []}

    def _parse_summary(self, data: Dict[str, Any]) -> Dict[str, Any]:
        risks = data.get("risks", [])
        findings = []
        for risk in risks:
            level = risk.get("level", "warn")
            severity = "HIGH" if level == "danger" else "MEDIUM" if level == "warn" else "LOW"
            findings.append({
                "source": "rugcheck",
                "title": risk.get("name", "Unknown Risk"),
                "description": risk.get("description", ""),
                "severity": severity,
                "score": risk.get("score", 0),
            })

        return {
            "mint": data.get("mint", ""),
            "score": data.get("score", 0),
            "score_normalised": data.get("score_normalised", 0),
            "verdict": self._verdict_from_score(data.get("score_normalised", 0)),
            "risks": risks,
            "findings": findings,
            "lp_locked_pct": data.get("lpLockedPct", 0),
            "token_type": data.get("tokenType", ""),
            "token_program": data.get("tokenProgram", ""),
            "totalHolders": data.get("totalHolders", 0),
            "locks": data.get("locks", []),
        }

    def _verdict_from_score(self, score_normalised: int) -> str:
        if score_normalised >= 70:
            return "DANGER"
        elif score_normalised >= 40:
            return "WARNING"
        return "GOOD"