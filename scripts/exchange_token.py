"""
Exchange OAuth code for token. Run IMMEDIATELY after getting callback URL.

Usage:
  python scripts/exchange_token.py 'https://oauth.pstmn.io/v1/callback?code=...&state=...'
  python scripts/exchange_token.py 'postman://app/oauth2/callback?code=...&state=...'
"""
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs

import httpx

TOKEN_FILE = Path("data/zomato_oauth.json")


async def exchange(callback_url: str):
    # Load saved state
    state_data = json.loads(TOKEN_FILE.read_text())
    if not state_data.get("pending_auth"):
        print("ERROR: No pending auth. Run the auth setup first.")
        return

    # Parse code from callback URL (handles both postman:// and https://)
    query_string = callback_url.split("?", 1)[1] if "?" in callback_url else ""
    params = parse_qs(query_string)
    code = params.get("code", [""])[0]
    cb_state = params.get("state", [""])[0]

    if not code:
        print("ERROR: No code found in URL")
        return

    if cb_state != state_data["state"]:
        print(f"WARNING: State mismatch. Expected: {state_data['state'][:20]}... Got: {cb_state[:20]}...")
        print("Continuing anyway (Zomato may not validate state)...")

    print(f"Code: {code[:20]}...")
    print("Exchanging for token...")

    async with httpx.AsyncClient(timeout=30) as c:
        token_resp = await c.post(
            "https://mcp-server.zomato.com/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": state_data["redirect_uri"],
                "client_id": state_data["client_id"],
                "code_verifier": state_data["code_verifier"],
            },
        )
        print(f"Token response: {token_resp.status_code}")

        if token_resp.status_code == 200:
            tokens = token_resp.json()
            # Save tokens
            state_data["pending_auth"] = False
            state_data["tokens"] = tokens
            TOKEN_FILE.write_text(json.dumps(state_data, indent=2))
            print(f"SUCCESS! Tokens saved to {TOKEN_FILE}")
            print(f"  access_token: {tokens.get('access_token', '')[:30]}...")
            print(f"  token_type: {tokens.get('token_type', 'N/A')}")
            print(f"  expires_in: {tokens.get('expires_in', 'N/A')} seconds")
            if tokens.get("refresh_token"):
                print(f"  refresh_token: present")

            # Now test connection
            print("\nTesting MCP connection with token...")
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            access_token = tokens["access_token"]
            http = httpx.AsyncClient(
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30,
            )
            try:
                async with streamable_http_client(
                    "https://mcp-server.zomato.com/mcp", http_client=http
                ) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        print(f"\nConnected! Available tools ({len(tools.tools)}):")
                        for t in tools.tools:
                            print(f"  {t.name}: {(t.description or '')[:80]}")
            except Exception as e:
                print(f"MCP connection test failed: {e}")
                print("But tokens are saved — they may work on retry.")
        else:
            print(f"FAILED: {token_resp.text}")
            print("\nThe code may have expired. You need to:")
            print("1. Re-run the auth setup (generates new URL)")
            print("2. Open the URL in browser")
            print("3. IMMEDIATELY paste the callback URL")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/exchange_token.py '<callback_url>'")
        sys.exit(1)
    asyncio.run(exchange(sys.argv[1]))
