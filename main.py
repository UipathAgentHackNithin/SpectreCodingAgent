import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from spectre_coding.agent import fix, FixIn, FixOut

__all__ = ["fix", "FixIn", "FixOut"]

if __name__ == "__main__":
    import asyncio
    result = asyncio.run(fix(FixIn(
        transaction_id="INV-98766",
        process_name="3201 Invoice Processing",
        diagnosis="SAP login failed due to credential timeout",
        recommended_action="Rotate SAP credentials and add retry logic",
        confidence="High",
    )))
    print(result)
