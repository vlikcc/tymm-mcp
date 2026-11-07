"""MCP stdio üzerinden uçtan uca test: sunucuyu alt süreç olarak başlatır."""
import asyncio, sys
from pathlib import Path
from mcp import Client, StdioServerParameters

ROOT = Path(__file__).resolve().parent.parent
server = StdioServerParameters(command=str(ROOT / ".venv/bin/tymm-mcp"))

async def main():
    async with Client(server) as c:
        print("instructions:", (c.instructions or "")[:80], "…")
        tools = (await c.list_tools()).tools
        print(f"{len(tools)} araç:", ", ".join(t.name for t in tools))
        for name, args in [
            ("ders_ara", {"sorgu": "fizik"}),
            ("uniteleri_listele", {"ders": "fizik-dersi", "sinif_id": 12}),
            ("kitap_ara", {"sorgu": "öğretmen kılavuz", "sinif_id": 11, "limit": 3}),
        ]:
            r = await c.call_tool(name, args)
            text = r.content[0].text
            print(f"\n--- {name} {args} (is_error={r.is_error})\n{text[:600]}")
            assert not r.is_error and not text.startswith("Hata"), text
    print("\nOK")

asyncio.run(main())
